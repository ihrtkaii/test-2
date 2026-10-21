"""Company career boards (the same registry secnet-internships scans).

Every `parse_*` function is pure (payload in, Jobs out) so the tests can run
against saved fixtures. The `fetch_*` coroutines add the network and decide
whether the read was COMPLETE: only a complete read may close a role.

Covered: Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Rippling, Breezy,
Recruitee. Not covered: Workable (its anonymous API answers bulk readers with a
provider-wide 429), Oracle, iCIMS and Eightfold.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime, timedelta

from .. import models
from ..models import Fetch, Job
from ..net import Net
from .base import Ctx

_TAGS = re.compile(r"<[^>]+>")


def plain(text: str | None) -> str | None:
    if not text:
        return None
    out = re.sub(r"\s+", " ", _TAGS.sub(" ", html.unescape(html.unescape(text)))).strip()
    return out or None


def norm_type(raw: str | None) -> str:
    """Map any ATS's employment vocabulary onto models.* constants."""
    t = re.sub(r"[\s_-]+", "", (raw or "").lower())
    if not t:
        return models.UNKNOWN
    if "part" in t:
        return models.PART_TIME
    if "intern" in t or "coop" in t or "student" in t:
        return models.INTERN
    if "contract" in t or "freelance" in t or "consult" in t:
        return models.CONTRACT
    if "temp" in t or "seasonal" in t or "perdiem" in t:
        return models.TEMPORARY
    if "full" in t or "permanent" in t or "regular" in t:
        return models.FULL_TIME
    return models.UNKNOWN


def _iso_ms(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


def _str(v) -> str | None:
    return str(v) if v not in (None, "") else None


# --------------------------------------------------------------------------- Greenhouse
GH_LIST = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def parse_greenhouse(payload: dict, company: dict) -> list[Job]:
    slug = company["slug"]
    out = []
    for p in (payload or {}).get("jobs") or []:
        if not isinstance(p, dict):
            continue
        loc = (p.get("location") or {}).get("name") if isinstance(p.get("location"), dict) else ""
        etype = models.UNKNOWN
        for meta in p.get("metadata") or []:
            if isinstance(meta, dict) and re.search(r"employment|time type|job type",
                                                     str(meta.get("name")), re.I):
                val = meta.get("value")
                etype = norm_type(val if isinstance(val, str) else " ".join(map(str, val or [])))
        out.append(Job(
            id=f"greenhouse:{slug}:{p.get('id')}", source="greenhouse",
            company=company["name"], title=(p.get("title") or "").strip(),
            location=(loc or "").strip() or "—", url=p.get("absolute_url") or "",
            posted_at=p.get("first_published") or p.get("updated_at"),
            employment_type=etype, requisition_id=_str(p.get("requisition_id")),
            updated_sig=_str(p.get("updated_at")), description=plain(p.get("content")),
        ))
    return out


def _listing_ok(payload, key: str) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get(key), list) and not payload.get("error")


async def fetch_greenhouse(company: dict, net: Net, ctx: Ctx) -> Fetch:
    """List first (cheap). If any wanted role is missing from the cache,
    re-read the whole board once with content=true: one request for every
    description, instead of one per role."""
    url = GH_LIST.format(slug=company["slug"])
    payload = await net.get_json(url)
    if not _listing_ok(payload, "jobs"):
        return Fetch([], ok=False, error="malformed listing")
    jobs = parse_greenhouse(payload, company)
    wanted = [j for j in jobs if ctx.want(j)]
    uncached = []
    for j in wanted:
        text = ctx.cache.get(j)
        if text is None:
            uncached.append(j)
        else:
            j.description = text
    if uncached:
        try:
            full = parse_greenhouse(await net.get_json(url, params={"content": "true"}), company)
        except Exception:  # noqa: BLE001 - the list itself is still good
            full = []
        texts = {j.id: j.description for j in full}
        for j in uncached:
            if texts.get(j.id):
                j.description = texts[j.id]
                ctx.cache.put(j, j.description)
    return Fetch(jobs)


# --------------------------------------------------------------------------- Lever
LEVER = "https://api.lever.co/v0/postings/{slug}?mode=json"


def _lever_salary(p: dict) -> str | None:
    rng = p.get("salaryRange")
    if not isinstance(rng, dict) or not rng.get("min"):
        return None
    unit = "/hr" if "hour" in str(rng.get("interval", "")).lower() else "/yr"
    lo, hi = int(rng["min"]), int(rng.get("max") or rng["min"])
    return f"${lo:,}" + (f"–${hi:,}" if hi != lo else "") + unit


def parse_lever(payload: list, company: dict) -> list[Job]:
    slug = company["slug"]
    out = []
    for p in payload or []:
        if not isinstance(p, dict):
            continue
        cats = p.get("categories") or {}
        workplace = str(p.get("workplaceType") or "").lower()
        desc = " ".join(filter(None, [
            p.get("descriptionPlain"), p.get("additionalPlain"),
            *(plain(x.get("content")) or "" for x in p.get("lists") or [] if isinstance(x, dict)),
        ]))
        out.append(Job(
            id=f"lever:{slug}:{p.get('id')}", source="lever",
            company=company["name"], title=(p.get("text") or "").strip(),
            location=(cats.get("location") or "—").strip() or "—",
            url=p.get("hostedUrl") or p.get("applyUrl") or "",
            posted_at=_iso_ms(p.get("createdAt")),
            employment_type=norm_type(cats.get("commitment")),
            remote=True if workplace == "remote" else None,
            hybrid=True if workplace == "hybrid" else None,
            salary=_lever_salary(p), description=desc or None,
        ))
    return out


async def fetch_lever(company: dict, net: Net, ctx: Ctx) -> Fetch:
    payload = await net.get_json(LEVER.format(slug=company["slug"]))
    if not isinstance(payload, list):
        return Fetch([], ok=False, error="malformed listing")
    return Fetch(parse_lever(payload, company))


# --------------------------------------------------------------------------- Ashby
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def parse_ashby(payload: dict, company: dict) -> list[Job]:
    slug = company["slug"]
    out = []
    for p in (payload or {}).get("jobs") or []:
        if not isinstance(p, dict) or p.get("isListed") is False:
            continue
        url = p.get("jobUrl") or p.get("applyUrl") or ""
        ext = url.rstrip("/").rsplit("/", 1)[-1] if url else p.get("id")
        workplace = str(p.get("workplaceType") or "").lower()
        locs = [p.get("location") or ""] + [
            (s.get("location") if isinstance(s, dict) else str(s))
            for s in p.get("secondaryLocations") or []
        ]
        comp = p.get("compensation") or {}
        out.append(Job(
            id=f"ashby:{slug}:{ext}", source="ashby",
            company=company["name"], title=(p.get("title") or "").strip(),
            location="; ".join(x for x in locs if x) or "—", url=url,
            posted_at=p.get("publishedAt"), employment_type=norm_type(p.get("employmentType")),
            remote=True if (p.get("isRemote") or workplace == "remote") else None,
            hybrid=True if workplace == "hybrid" else None,
            salary=(comp.get("compensationTierSummary") if isinstance(comp, dict) else None),
            description=p.get("descriptionPlain") or plain(p.get("descriptionHtml")),
        ))
    return out


async def fetch_ashby(company: dict, net: Net, ctx: Ctx) -> Fetch:
    payload = await net.get_json(ASHBY.format(slug=company["slug"]))
    if not _listing_ok(payload, "jobs"):
        return Fetch([], ok=False, error="malformed listing")
    return Fetch(parse_ashby(payload, company))


# --------------------------------------------------------------------------- SmartRecruiters
SR = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
SR_QUERIES = ("security", "cyber", "part time")
SR_PAGE = 100
SR_PAGES = 5


def parse_smartrecruiters(payload: dict, company: dict) -> list[Job]:
    slug = company["slug"]
    out = []
    for p in (payload or {}).get("content") or []:
        if not isinstance(p, dict):
            continue
        loc = p.get("location") or {}
        text = loc.get("fullLocation") or ", ".join(
            x for x in (loc.get("city"), loc.get("region"), str(loc.get("country") or "").upper()) if x)
        emp = p.get("typeOfEmployment") or {}
        out.append(Job(
            id=f"smartrecruiters:{slug}:{p.get('id')}", source="smartrecruiters",
            company=company["name"],
            title=(p.get("name") or "").strip(), location=text or "—",
            url=f"https://jobs.smartrecruiters.com/{slug}/{p.get('id')}",
            posted_at=p.get("releasedDate"),
            employment_type=norm_type(emp.get("label") or emp.get("id")
                                      if isinstance(emp, dict) else str(emp)),
            remote=True if loc.get("remote") else None,
            hybrid=True if loc.get("hybrid") else None,
            requisition_id=_str(p.get("refNumber")), updated_sig=_str(p.get("releasedDate")),
        ))
    return out


async def fetch_smartrecruiters(company: dict, net: Net, ctx: Ctx) -> Fetch:
    base = SR.format(slug=company["slug"])
    seen: dict[str, Job] = {}
    complete = True
    for q in SR_QUERIES:
        exhausted = False
        for page in range(SR_PAGES):
            data = await net.get_json(base, params={"q": q, "limit": SR_PAGE,
                                                    "offset": page * SR_PAGE})
            if not _listing_ok(data, "content"):
                if not seen and page == 0 and q == SR_QUERIES[0]:
                    return Fetch([], ok=False, error="malformed listing")
                break
            rows = parse_smartrecruiters(data, company)
            for job in rows:
                seen.setdefault(job.id, job)
            total = data.get("totalFound")
            if len(rows) < SR_PAGE or (isinstance(total, int) and (page + 1) * SR_PAGE >= total):
                exhausted = True
                break
        complete = complete and exhausted
    for job in seen.values():
        if ctx.want(job):
            async def load(job=job):
                d = await net.get_json(f"{base}/{job.id.rsplit(':', 1)[-1]}")
                sections = ((d or {}).get("jobAd") or {}).get("sections") or {}
                return plain(" ".join(str(s.get("text") or "") for s in sections.values()
                                      if isinstance(s, dict)))
            await ctx.describe(job, load)
    return Fetch(list(seen.values()), complete=complete)


# --------------------------------------------------------------------------- Workday
WD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}
WD_SECURITY_TERMS = ("cyber", "security")
WD_STUDENT_TERMS = ("intern",)
WD_PRIORITY_EXTRA = ("student", "co-op", "information technology")
WD_PAGE = 20
WD_PAGES = 5
_DAYS_AGO = re.compile(r"(\d+)\s*\+?\s*days?\s+ago", re.IGNORECASE)

# Outcome codes, counted in stats.json so "Workday failed" is never one number.
WD_UNAVAILABLE = "tenant-unavailable"
WD_FACET_FOUND = "facet-found"
WD_FACET_MISSING = "facet-missing"
WD_FACET_FAILED = "facet-query-failed"
WD_ZERO = "zero-matches"
WD_CAPPED = "capped"
WD_PAGE_FAILED = "page-failed"


def wd_urls(company: dict) -> tuple[str, str]:
    tenant, site = company["slug"], company["site"]
    host = company.get("host")
    if host:
        return (f"https://{host}/wday/cxs/{tenant}/{site}", f"https://{host}/recruiting/{tenant}/{site}")
    sub = f"{tenant}.{company['wd']}.myworkdayjobs.com"
    return (f"https://{sub}/wday/cxs/{tenant}/{site}", f"https://{sub}/{site}")


def _wd_posted(text: str | None) -> str | None:
    low = (text or "").lower()
    if "today" in low:
        days = 0
    elif "yesterday" in low:
        days = 1
    else:
        m = _DAYS_AGO.search(low)
        if not m or "30+" in low:
            return None
        days = int(m.group(1))
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


def wd_part_time_facet(payload: dict) -> tuple[str, str] | None:
    """(facetParameter, value id) for the tenant's "Part time" filter, if any.
    Facets nest one level on some tenants, so walk both."""
    def walk(facets):
        for f in facets or []:
            if not isinstance(f, dict):
                continue
            param = str(f.get("facetParameter") or "")
            for v in f.get("values") or []:
                if not isinstance(v, dict):
                    continue
                if "facetParameter" in v:  # nested group
                    hit = walk([v])
                    if hit:
                        return hit
                    continue
                if re.search(r"time\s*type|timetype|worker\s*sub\s*type", param, re.I) and \
                        re.search(r"part", str(v.get("descriptor")), re.I) and v.get("id"):
                    return (param, v["id"])
        return None
    return walk((payload or {}).get("facets"))


def parse_workday_list(payload: dict, company: dict, base: str) -> list[Job]:
    tenant = company["slug"]
    out = []
    for p in (payload or {}).get("jobPostings") or []:
        if not isinstance(p, dict):
            continue
        path = p.get("externalPath") or ""
        out.append(Job(
            id=f"workday:{tenant}:{path or p.get('title')}", source="workday",
            company=company["name"], title=(p.get("title") or "").strip(),
            location=(p.get("locationsText") or "—").strip() or "—",
            url=(base + path) if path else base, posted_at=_wd_posted(p.get("postedOn")),
            remote=True if re.search(r"remote", str(p.get("remoteType") or ""), re.I) else None,
        ))
    return out


def apply_workday_detail(job: Job, payload: dict) -> str | None:
    """Copy detail fields onto `job`; return the description text."""
    info = (payload or {}).get("jobPostingInfo") or {}
    if not isinstance(info, dict):
        return None
    etype = norm_type(info.get("timeType"))
    if etype != models.UNKNOWN:
        job.employment_type = etype
    remote_type = str(info.get("remoteType") or "")
    if re.search(r"hybrid", remote_type, re.I):
        job.hybrid = True
    elif re.search(r"remote", remote_type, re.I):
        job.remote = True
    locs = [str(x) for x in [info.get("location"), *(info.get("additionalLocations") or [])] if x]
    if locs and (job.location in ("—", "") or "location" in job.location.lower()):
        job.location = "; ".join(locs)  # "2 Locations" -> the real list
    if info.get("jobReqId"):
        job.requisition_id = str(info["jobReqId"])
    return plain(info.get("jobDescription"))


async def _wd_search(net: Net, api: str, term: str, facet) -> tuple[list[dict], bool, str | None]:
    """All pages for one search. Returns (pages, exhausted, failure code)."""
    body = {"appliedFacets": {facet[0]: [facet[1]]} if facet else {},
            "limit": WD_PAGE, "offset": 0, "searchText": term}
    pages = []
    for i in range(WD_PAGES):
        body["offset"] = i * WD_PAGE
        try:
            page = await net.post_json(f"{api}/jobs", json=body, headers=WD_HEADERS)
        except Exception:  # noqa: BLE001
            return pages, False, (WD_FACET_FAILED if facet and i == 0 else WD_PAGE_FAILED)
        if not _listing_ok(page, "jobPostings"):
            return pages, False, WD_PAGE_FAILED
        pages.append(page)
        rows = page["jobPostings"]
        total = page.get("total")
        if len(rows) < WD_PAGE or (isinstance(total, int) and total and
                                   (i + 1) * WD_PAGE >= total):
            return pages, True, None
    return pages, False, WD_CAPPED


async def fetch_workday(company: dict, net: Net, ctx: Ctx) -> Fetch:
    """Pass A: security/cyber searches through the tenant's Part-time facet
    (precise). Pass B: unfiltered student searches ("intern"; for priority
    employers also "student", "co-op", and security without the facet), to
    catch student roles the facet misses. Merged by job id."""
    api, base = wd_urls(company)
    notes: list[str] = []
    jobs: dict[str, Job] = {}
    complete = True
    priority = ctx.priority(company["name"])

    # The first request tells us if the tenant is up and what facets it has.
    probe_body = {"appliedFacets": {}, "limit": WD_PAGE, "offset": 0,
                  "searchText": WD_SECURITY_TERMS[0]}
    try:
        probe = await net.post_json(f"{api}/jobs", json=probe_body, headers=WD_HEADERS)
    except Exception as exc:  # noqa: BLE001
        return Fetch([], ok=False, error=f"{WD_UNAVAILABLE}: {type(exc).__name__}",
                     notes=[WD_UNAVAILABLE])
    if not _listing_ok(probe, "jobPostings"):
        return Fetch([], ok=False, error=f"{WD_UNAVAILABLE}: malformed", notes=[WD_UNAVAILABLE])
    facet = wd_part_time_facet(probe)
    notes.append(WD_FACET_FOUND if facet else WD_FACET_MISSING)

    plan: list[tuple[str, tuple | None, bool]] = []  # (term, facet, marks part-time)
    for term in WD_SECURITY_TERMS:
        if facet:
            plan.append((term, facet, True))
            if priority:
                plan.append((term, None, False))
        else:
            plan.append((term, None, False))
    for term in WD_STUDENT_TERMS + (WD_PRIORITY_EXTRA if priority else ()):
        plan.append((term, None, False))

    for term, use_facet, part_time in plan:
        pages, exhausted, failure = await _wd_search(net, api, term, use_facet)
        if failure == WD_FACET_FAILED:
            # The filter itself broke; fall back to the plain search.
            notes.append(failure)
            pages, exhausted, failure = await _wd_search(net, api, term, None)
            part_time = False
        if failure:
            notes.append(failure)
        if not exhausted:
            complete = False
        for page in pages:
            for job in parse_workday_list(page, company, base):
                prior = jobs.get(job.id)
                if part_time:
                    job.employment_type = models.PART_TIME
                if prior is None:
                    jobs[job.id] = job
                elif part_time:
                    prior.employment_type = models.PART_TIME
    if not jobs and complete:
        notes.append(WD_ZERO)

    for job in jobs.values():
        if not ctx.want(job):
            continue
        path = job.url[len(base):] if job.url.startswith(base) else ""
        if not path:
            continue
        cached = ctx.cache.get(job)
        if cached is not None:
            job.description = cached
            continue
        known = job.employment_type
        try:
            text = apply_workday_detail(job, await net.get_json(api + path, headers=WD_HEADERS))
        except Exception:  # noqa: BLE001 - detail is best-effort
            text = None
        if known == models.PART_TIME and job.employment_type == models.UNKNOWN:
            job.employment_type = known
        if text:
            job.description = text
            ctx.cache.put(job, text)
    return Fetch(list(jobs.values()), complete=complete, notes=notes)


# --------------------------------------------------------------------------- Rippling / Breezy / Recruitee
RIPPLING = "https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
BREEZY = "https://{slug}.breezy.hr/json"
RECRUITEE = "https://{slug}.recruitee.com/api/offers/"


def parse_rippling(payload: list, company: dict) -> list[Job]:
    slug = company["slug"]
    out = []
    for p in payload or []:
        if not isinstance(p, dict):
            continue
        loc = p.get("workLocation") or {}
        emp = p.get("employmentType")
        out.append(Job(
            id=f"rippling:{slug}:{p.get('uuid')}", source="rippling",
            company=company["name"], title=(p.get("name") or "").strip(),
            location=((loc.get("label") if isinstance(loc, dict) else "") or "—").strip(),
            url=p.get("url") or "",
            employment_type=norm_type(emp.get("label") if isinstance(emp, dict) else emp),
        ))
    return out


def parse_breezy(payload: list, company: dict) -> list[Job]:
    slug = company["slug"]
    out = []
    for p in payload or []:
        if not isinstance(p, dict):
            continue
        loc = p.get("location") or {}
        typ = p.get("type") or {}
        out.append(Job(
            id=f"breezy:{slug}:{p.get('id')}", source="breezy",
            company=company["name"], title=(p.get("name") or "").strip(),
            location=((loc.get("name") if isinstance(loc, dict) else str(loc)) or "—").strip(),
            url=p.get("url") or "", posted_at=p.get("published_date"),
            employment_type=norm_type(typ.get("id") or typ.get("name")
                                      if isinstance(typ, dict) else str(typ)),
            remote=True if isinstance(loc, dict) and loc.get("is_remote") else None,
            salary=p.get("salary") if isinstance(p.get("salary"), str) and p.get("salary") else None,
        ))
    return out


def parse_recruitee(payload: dict, company: dict) -> list[Job]:
    slug = company["slug"]
    out = []
    for p in (payload or {}).get("offers") or []:
        if not isinstance(p, dict):
            continue
        hours = ""
        if p.get("min_hours") or p.get("max_hours"):
            hours = (f" {p.get('min_hours') or ''}-{p.get('max_hours') or p.get('min_hours')}"
                     " hours per week")
        out.append(Job(
            id=f"recruitee:{slug}:{p.get('id')}", source="recruitee",
            company=company["name"], title=(p.get("title") or "").strip(),
            location=(p.get("location") or "—").strip() or "—",
            url=p.get("careers_url") or "", posted_at=p.get("published_at"),
            employment_type=norm_type(p.get("employment_type_code")),
            remote=True if p.get("remote") else None, hybrid=True if p.get("hybrid") else None,
            description=((plain(p.get("description")) or "") + hours) or None,
        ))
    return out


async def fetch_rippling(company, net, ctx):
    payload = await net.get_json(RIPPLING.format(slug=company["slug"]))
    if not isinstance(payload, list):
        return Fetch([], ok=False, error="malformed listing")
    return Fetch(parse_rippling(payload, company))


async def fetch_breezy(company, net, ctx):
    payload = await net.get_json(BREEZY.format(slug=company["slug"]))
    if not isinstance(payload, list):
        return Fetch([], ok=False, error="malformed listing")
    return Fetch(parse_breezy(payload, company))


async def fetch_recruitee(company, net, ctx):
    payload = await net.get_json(RECRUITEE.format(slug=company["slug"]))
    if not _listing_ok(payload, "offers"):
        return Fetch([], ok=False, error="malformed listing")
    return Fetch(parse_recruitee(payload, company))


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "rippling": fetch_rippling,
    "breezy": fetch_breezy,
    "recruitee": fetch_recruitee,
}

# Anything that changes WHAT a board search returns. When it changes between
# runs, absence proves nothing, so the pipeline closes nothing that run.
SCOPE = {
    "sr": SR_QUERIES, "wd_sec": WD_SECURITY_TERMS, "wd_student": WD_STUDENT_TERMS,
    "wd_priority": WD_PRIORITY_EXTRA,
}
