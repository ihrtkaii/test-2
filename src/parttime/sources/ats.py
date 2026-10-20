"""Company career boards (the same registry secnet-internships scans).

Every `parse_*` function is pure — payload in, Jobs out — so the tests can run
against saved fixtures. The `fetch_*` coroutines only add the network.

Covered: Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Rippling, Breezy,
Recruitee. Not covered: Workable (its anonymous API answers bulk readers with a
provider-wide 429), Oracle, iCIMS and Eightfold (keyword search only, few
security part-time roles).
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime, timedelta

from .. import models
from ..models import Fetch, Job
from ..net import Net

_TAGS = re.compile(r"<[^>]+>")


def plain(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", _TAGS.sub(" ", html.unescape(html.unescape(text)))).strip()


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
            board=f"greenhouse:{slug}", company=company["name"],
            title=(p.get("title") or "").strip(), location=(loc or "").strip() or "—",
            url=p.get("absolute_url") or "", posted_at=p.get("first_published") or p.get("updated_at"),
            employment_type=etype, description=plain(p.get("content")),
        ))
    return out


async def fetch_greenhouse(company: dict, net: Net, want) -> Fetch:
    """List first (cheap); if any title is a candidate, re-read the board once
    with content=true so descriptions come in one request, not one per job."""
    url = GH_LIST.format(slug=company["slug"])
    jobs = parse_greenhouse(await net.get_json(url), company)
    if any(want(j) for j in jobs):
        full = parse_greenhouse(
            await net.get_json(url, params={"content": "true"}), company)
        if full:
            jobs = full
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
            id=f"lever:{slug}:{p.get('id')}", source="lever", board=f"lever:{slug}",
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


async def fetch_lever(company: dict, net: Net, want) -> Fetch:
    return Fetch(parse_lever(await net.get_json(LEVER.format(slug=company["slug"])), company))


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
            id=f"ashby:{slug}:{ext}", source="ashby", board=f"ashby:{slug}",
            company=company["name"], title=(p.get("title") or "").strip(),
            location="; ".join(x for x in locs if x) or "—", url=url,
            posted_at=p.get("publishedAt"), employment_type=norm_type(p.get("employmentType")),
            remote=True if (p.get("isRemote") or workplace == "remote") else None,
            hybrid=True if workplace == "hybrid" else None,
            salary=(comp.get("compensationTierSummary") if isinstance(comp, dict) else None),
            description=p.get("descriptionPlain") or plain(p.get("descriptionHtml")),
        ))
    return out


async def fetch_ashby(company: dict, net: Net, want) -> Fetch:
    return Fetch(parse_ashby(await net.get_json(ASHBY.format(slug=company["slug"])), company))


# --------------------------------------------------------------------------- SmartRecruiters
SR = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
SR_QUERIES = ("security", "cyber", "part time")


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
            board=f"smartrecruiters:{slug}", company=company["name"],
            title=(p.get("name") or "").strip(), location=text or "—",
            url=f"https://jobs.smartrecruiters.com/{slug}/{p.get('id')}",
            posted_at=p.get("releasedDate"),
            employment_type=norm_type(emp.get("label") or emp.get("id")
                                      if isinstance(emp, dict) else str(emp)),
            remote=True if loc.get("remote") else None,
            hybrid=True if loc.get("hybrid") else None,
        ))
    return out


async def fetch_smartrecruiters(company: dict, net: Net, want) -> Fetch:
    seen: dict[str, Job] = {}
    for q in SR_QUERIES:
        data = await net.get_json(SR.format(slug=company["slug"]),
                                  params={"q": q, "limit": 100})
        for job in parse_smartrecruiters(data, company):
            seen.setdefault(job.id, job)
    # The list has no description; one detail call per candidate fills it.
    for job in seen.values():
        if want(job):
            pid = job.id.rsplit(":", 1)[-1]
            try:
                d = await net.get_json(f"{SR.format(slug=company['slug'])}/{pid}")
            except Exception:  # noqa: BLE001 - detail is best-effort
                continue
            sections = ((d or {}).get("jobAd") or {}).get("sections") or {}
            job.description = plain(" ".join(
                str(s.get("text") or "") for s in sections.values() if isinstance(s, dict)))
    return Fetch(list(seen.values()))


# --------------------------------------------------------------------------- Workday
WD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}
WD_TERMS = ("cyber", "security", "intern")
WD_PAGE = 20
WD_PAGES = 5
_DAYS_AGO = re.compile(r"(\d+)\s*\+?\s*days?\s+ago", re.IGNORECASE)


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
            board=f"workday:{tenant}:{company.get('site')}", company=company["name"],
            title=(p.get("title") or "").strip(),
            location=(p.get("locationsText") or "—").strip() or "—",
            url=(base + path) if path else base, posted_at=_wd_posted(p.get("postedOn")),
            remote=True if re.search(r"remote", str(p.get("remoteType") or ""), re.I) else None,
        ))
    return out


def apply_workday_detail(job: Job, payload: dict) -> None:
    info = (payload or {}).get("jobPostingInfo") or {}
    if not isinstance(info, dict):
        return
    job.description = plain(info.get("jobDescription"))
    job.employment_type = norm_type(info.get("timeType"))
    remote_type = str(info.get("remoteType") or "")
    if re.search(r"remote", remote_type, re.I) and not re.search(r"hybrid", remote_type, re.I):
        job.remote = True
    elif re.search(r"hybrid", remote_type, re.I):
        job.hybrid = True
    extra = [info.get("location")] + list(info.get("additionalLocations") or [])
    locs = [str(x) for x in extra if x]
    if locs and (job.location in ("—", "") or "location" in job.location.lower()):
        job.location = "; ".join(locs)  # "2 Locations" -> the real list
    if info.get("startDate") and not job.posted_at:
        job.posted_at = str(info["startDate"])[:10]


async def fetch_workday(company: dict, net: Net, want) -> Fetch:
    api, base = wd_urls(company)
    jobs: dict[str, Job] = {}
    for term in WD_TERMS:
        body = {"appliedFacets": {}, "limit": WD_PAGE, "offset": 0, "searchText": term}
        first = await net.post_json(f"{api}/jobs", json=body, headers=WD_HEADERS)
        facet = wd_part_time_facet(first)
        pages = [first]
        if facet and term != "intern":
            # Re-ask with the tenant's own Part-time filter: precise, and
            # usually one page instead of five.
            body["appliedFacets"] = {facet[0]: [facet[1]]}
            pages = [await net.post_json(f"{api}/jobs", json=body, headers=WD_HEADERS)]
        total = (pages[0] or {}).get("total") or 0
        for offset in range(WD_PAGE, min(int(total or 0), WD_PAGE * WD_PAGES), WD_PAGE):
            body["offset"] = offset
            pages.append(await net.post_json(f"{api}/jobs", json=body, headers=WD_HEADERS))
        part_time_ids = set()
        for page in pages:
            for job in parse_workday_list(page, company, base):
                if facet and term != "intern":
                    job.employment_type = models.PART_TIME
                    part_time_ids.add(job.id)
                prior = jobs.get(job.id)
                if prior is None or job.id in part_time_ids:
                    jobs[job.id] = job
    for job in jobs.values():
        if want(job):
            path = job.url[len(base):] if job.url.startswith(base) else ""
            if not path:
                continue
            known = job.employment_type
            try:
                apply_workday_detail(job, await net.get_json(api + path, headers=WD_HEADERS))
            except Exception:  # noqa: BLE001 - detail is best-effort
                pass
            if known == models.PART_TIME and job.employment_type == models.UNKNOWN:
                job.employment_type = known
    return Fetch(list(jobs.values()))


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
        out.append(Job(
            id=f"rippling:{slug}:{p.get('uuid')}", source="rippling", board=f"rippling:{slug}",
            company=company["name"], title=(p.get("name") or "").strip(),
            location=((loc.get("label") if isinstance(loc, dict) else "") or "—").strip(),
            url=p.get("url") or "", employment_type=norm_type(
                (p.get("employmentType") or {}).get("label")
                if isinstance(p.get("employmentType"), dict) else p.get("employmentType")),
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
            id=f"breezy:{slug}:{p.get('id')}", source="breezy", board=f"breezy:{slug}",
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
            hours = f" {p.get('min_hours') or ''}-{p.get('max_hours') or p.get('min_hours')} hours per week"
        out.append(Job(
            id=f"recruitee:{slug}:{p.get('id')}", source="recruitee", board=f"recruitee:{slug}",
            company=company["name"], title=(p.get("title") or "").strip(),
            location=(p.get("location") or "—").strip() or "—",
            url=p.get("careers_url") or "", posted_at=p.get("published_at"),
            employment_type=norm_type(p.get("employment_type_code")),
            remote=True if p.get("remote") else None, hybrid=True if p.get("hybrid") else None,
            description=((plain(p.get("description")) or "") + hours) or None,
        ))
    return out


async def fetch_rippling(company, net, want):
    return Fetch(parse_rippling(await net.get_json(RIPPLING.format(slug=company["slug"])), company))


async def fetch_breezy(company, net, want):
    return Fetch(parse_breezy(await net.get_json(BREEZY.format(slug=company["slug"])), company))


async def fetch_recruitee(company, net, want):
    return Fetch(parse_recruitee(await net.get_json(RECRUITEE.format(slug=company["slug"])), company))


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
