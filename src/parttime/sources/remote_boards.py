"""Remote-only job boards with public JSON feeds. These are where most hourly
contract and freelance security work shows up.

Completeness: Remotive's search returns every match, so its reads are
complete. Jobicy caps at `count`, so a full page is a partial read. RemoteOK
only serves its most recent postings, so it is never complete: its roles go
stale instead of closing.
"""

from __future__ import annotations

from .. import models
from ..models import Fetch, Job
from ..net import Net
from .ats import norm_type, plain

REMOTIVE = "https://remotive.com/api/remote-jobs"
JOBICY = "https://jobicy.com/api/v2/remote-jobs"
REMOTEOK = "https://remoteok.com/api"
REMOTIVE_TERMS = ("security", "cyber", "soc analyst", "penetration")
JOBICY_TAGS = ("security", "cybersecurity")
JOBICY_COUNT = 100


def html_title(text) -> str:
    return (plain(str(text or "")) or "").strip()


def parse_remotive(payload: dict) -> list[Job]:
    out = []
    for p in (payload or {}).get("jobs") or []:
        if not isinstance(p, dict):
            continue
        region = (p.get("candidate_required_location") or "").strip()
        out.append(Job(
            id=f"remotive:{p.get('id')}", source="remotive",
            company=p.get("company_name") or "—", title=html_title(p.get("title")),
            location=f"Remote ({region})" if region else "Remote", url=p.get("url") or "",
            posted_at=(p.get("publication_date") or "")[:10] or None,
            employment_type=norm_type(p.get("job_type")), remote=True,
            salary=(p.get("salary") or None), description=plain(p.get("description")),
        ))
    return out


def parse_jobicy(payload: dict) -> list[Job]:
    out = []
    for p in (payload or {}).get("jobs") or []:
        if not isinstance(p, dict):
            continue
        types = p.get("jobType") or []
        types = types if isinstance(types, list) else [types]
        etype = models.UNKNOWN
        for t in types:  # listed as both full- and part-time: part-time is possible
            nt = norm_type(str(t))
            if nt == models.PART_TIME or etype == models.UNKNOWN:
                etype = nt
        salary = None
        if p.get("salaryMin"):
            per = str(p.get("salaryPeriod") or "").lower()
            salary = f"${int(float(p['salaryMin'])):,}" + (
                f"–${int(float(p['salaryMax'])):,}" if p.get("salaryMax") else "") + (
                "/hr" if "hour" in per else "/yr")
        geo = (p.get("jobGeo") or "").strip()
        out.append(Job(
            id=f"jobicy:{p.get('id')}", source="jobicy",
            company=p.get("companyName") or "—", title=html_title(p.get("jobTitle")),
            location=f"Remote ({geo})" if geo else "Remote", url=p.get("url") or "",
            posted_at=(p.get("pubDate") or "")[:10] or None, employment_type=etype,
            remote=True, salary=salary,
            description=plain(p.get("jobDescription") or p.get("jobExcerpt")),
        ))
    return out


def parse_remoteok(payload: list) -> list[Job]:
    out = []
    for p in payload or []:
        if not isinstance(p, dict) or not p.get("id") or not p.get("position"):
            continue  # first element is the API's legal notice
        tags = [str(t).lower() for t in p.get("tags") or []]
        etype = models.UNKNOWN
        if any("part" in t for t in tags):
            etype = models.PART_TIME
        elif any(t in ("contract", "freelance", "contractor") for t in tags):
            etype = models.CONTRACT
        loc = (p.get("location") or "").strip()
        salary = None
        if p.get("salary_min"):
            salary = f"${int(p['salary_min']):,}–${int(p.get('salary_max') or p['salary_min']):,}/yr"
        out.append(Job(
            id=f"remoteok:{p.get('id')}", source="remoteok",
            company=p.get("company") or "—", title=html_title(p.get("position")),
            location=f"Remote ({loc})" if loc else "Remote",
            url=p.get("url") or f"https://remoteok.com/remote-jobs/{p.get('id')}",
            posted_at=(p.get("date") or "")[:10] or None, employment_type=etype,
            remote=True, salary=salary, description=plain(p.get("description")),
        ))
    return out


async def fetch_remotive(net: Net) -> Fetch:
    seen: dict[str, Job] = {}
    for term in REMOTIVE_TERMS:
        data = await net.get_json(REMOTIVE, params={"search": term})
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            return Fetch(list(seen.values()), ok=bool(seen), complete=False,
                         error="malformed listing")
        for job in parse_remotive(data):
            seen.setdefault(job.id, job)
    return Fetch(list(seen.values()))


async def fetch_jobicy(net: Net) -> Fetch:
    seen: dict[str, Job] = {}
    complete = True
    for tag in JOBICY_TAGS:
        data = await net.get_json(JOBICY, params={"count": JOBICY_COUNT, "tag": tag})
        rows = parse_jobicy(data)
        if len(rows) >= JOBICY_COUNT:
            complete = False
        for job in rows:
            seen.setdefault(job.id, job)
    return Fetch(list(seen.values()), complete=complete)


async def fetch_remoteok(net: Net) -> Fetch:
    data = await net.get_json(REMOTEOK, headers={"Accept": "application/json"})
    if not isinstance(data, list):
        return Fetch([], ok=False, error="malformed listing")
    return Fetch(parse_remoteok(data), complete=False)


FETCHERS = {
    "remotive": fetch_remotive,
    "jobicy": fetch_jobicy,
    "remoteok": fetch_remoteok,
}
SCOPE = {"remotive": REMOTIVE_TERMS, "jobicy": JOBICY_TAGS}
