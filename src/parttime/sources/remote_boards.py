"""Remote-only job boards with public JSON feeds. These are where most hourly
contract and freelance security work shows up.

Remotive and Jobicy state the employment type; RemoteOK only has tags, so its
part-time evidence comes from tags and posting text.
"""

from __future__ import annotations

import re

from .. import models
from ..models import Fetch, Job
from ..net import Net
from .ats import norm_type, plain

REMOTIVE = "https://remotive.com/api/remote-jobs"
JOBICY = "https://jobicy.com/api/v2/remote-jobs"
REMOTEOK = "https://remoteok.com/api"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60]


def parse_remotive(payload: dict) -> list[Job]:
    out = []
    for p in (payload or {}).get("jobs") or []:
        if not isinstance(p, dict):
            continue
        region = (p.get("candidate_required_location") or "").strip()
        out.append(Job(
            id=f"remotive:{p.get('id')}", source="remotive", board="remotive",
            company=p.get("company_name") or "—", title=(p.get("title") or "").strip(),
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
        for t in types:  # a role listed as both full- and part-time is part-time-capable
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
            id=f"jobicy:{p.get('id')}", source="jobicy", board="jobicy",
            company=p.get("companyName") or "—", title=html_title(p.get("jobTitle")),
            location=f"Remote ({geo})" if geo else "Remote", url=p.get("url") or "",
            posted_at=(p.get("pubDate") or "")[:10] or None, employment_type=etype,
            remote=True, salary=salary,
            description=plain(p.get("jobDescription") or p.get("jobExcerpt")),
        ))
    return out


def html_title(text) -> str:
    return (plain(str(text or "")) or "").strip()


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
            id=f"remoteok:{p.get('id')}", source="remoteok", board="remoteok",
            company=p.get("company") or "—", title=html_title(p.get("position")),
            location=f"Remote ({loc})" if loc else "Remote",
            url=p.get("url") or f"https://remoteok.com/remote-jobs/{p.get('id')}",
            posted_at=(p.get("date") or "")[:10] or None, employment_type=etype,
            remote=True, salary=salary, description=plain(p.get("description")),
        ))
    return out


async def fetch_remotive(net: Net) -> Fetch:
    seen: dict[str, Job] = {}
    for term in ("security", "cyber", "soc analyst", "penetration"):
        for job in parse_remotive(await net.get_json(REMOTIVE, params={"search": term})):
            seen.setdefault(job.id, job)
    return Fetch(list(seen.values()))


async def fetch_jobicy(net: Net) -> Fetch:
    seen: dict[str, Job] = {}
    for tag in ("security", "cybersecurity"):
        data = await net.get_json(JOBICY, params={"count": 100, "tag": tag})
        for job in parse_jobicy(data):
            seen.setdefault(job.id, job)
    return Fetch(list(seen.values()))


async def fetch_remoteok(net: Net) -> Fetch:
    data = await net.get_json(REMOTEOK, headers={"Accept": "application/json"})
    return Fetch(parse_remoteok(data))


FETCHERS = {
    "remotive": fetch_remotive,
    "jobicy": fetch_jobicy,
    "remoteok": fetch_remoteok,
}
