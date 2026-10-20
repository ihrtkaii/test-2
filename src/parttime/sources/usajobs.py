"""USAJOBS (federal). Needs a free API key: https://developer.usajobs.gov/apirequest/

Set USAJOBS_API_KEY and USAJOBS_EMAIL as repo secrets. Without them this
source is skipped and the run summary says so.

Three searches, all in the 2210 IT Management series (where INFOSEC and
CYBERSECURITY roles live) plus a keyword search for other series:
  - part-time schedule (PositionScheduleTypeCode 2)
  - student hiring path (Pathways interns, often part-time during school)
  - keyword "cybersecurity" on the student path
Remote roles and roles in the Orlando area are kept by the shared filter.
"""

from __future__ import annotations

import os

from .. import models
from ..models import Fetch, Job
from ..net import Net
from .ats import plain

URL = "https://data.usajobs.gov/api/search"
QUERIES = (
    {"JobCategoryCode": "2210", "PositionScheduleTypeCode": "2"},
    {"JobCategoryCode": "2210", "HiringPath": "student"},
    {"Keyword": "cybersecurity", "HiringPath": "student"},
    {"Keyword": "cybersecurity", "PositionScheduleTypeCode": "2"},
)


def parse(payload: dict) -> list[Job]:
    items = (((payload or {}).get("SearchResult") or {}).get("SearchResultItems")) or []
    out = []
    for item in items:
        d = (item or {}).get("MatchedObjectDescriptor") or {}
        if not d:
            continue
        details = ((d.get("UserArea") or {}).get("Details")) or {}
        schedule = " ".join(str(s.get("Name", "")) for s in d.get("PositionSchedule") or [])
        pay = (d.get("PositionRemuneration") or [{}])[0] or {}
        salary = None
        if pay.get("MinimumRange"):
            unit = "/hr" if pay.get("RateIntervalCode") == "PH" else "/yr"
            lo, hi = float(pay["MinimumRange"]), float(pay.get("MaximumRange") or pay["MinimumRange"])
            fmt = (lambda v: f"${v:,.2f}") if unit == "/hr" else (lambda v: f"${v:,.0f}")
            salary = fmt(lo) + (f"–{fmt(hi)}" if hi != lo else "") + unit
        remote = str(details.get("RemoteIndicator")).lower() == "true"
        title = (d.get("PositionTitle") or "").strip()
        # Federal titles are generic ("IT Specialist (INFOSEC)" passes, plain
        # "IT Specialist (CUSTSPT)" does not). Put the series tag the posting
        # carries into the title the filter reads, so INFOSEC roles survive.
        text = " ".join(filter(None, [
            details.get("JobSummary"), d.get("QualificationSummary"), schedule,
            " ".join(details.get("MajorDuties") or []),
            "Security clearance: " + str(details.get("SecurityClearance") or ""),
        ]))
        out.append(Job(
            id=f"usajobs:{d.get('PositionID') or d.get('PositionURI')}", source="usajobs",
            board="usajobs", company=d.get("OrganizationName") or d.get("DepartmentName") or "Federal",
            title=title, location=d.get("PositionLocationDisplay") or "—",
            url=d.get("PositionURI") or "",
            posted_at=(d.get("PublicationStartDate") or "")[:10] or None,
            employment_type=models.PART_TIME if "part" in schedule.lower() else models.UNKNOWN,
            remote=remote or None, salary=salary, description=plain(text),
        ))
    return out


async def fetch(net: Net) -> Fetch:
    key, email = os.environ.get("USAJOBS_API_KEY"), os.environ.get("USAJOBS_EMAIL")
    if not key or not email:
        return Fetch([], ok=False, error="skipped: USAJOBS_API_KEY / USAJOBS_EMAIL not set")
    headers = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key}
    seen: dict[str, Job] = {}
    for q in QUERIES:
        data = await net.get_json(URL, params={**q, "ResultsPerPage": 500}, headers=headers)
        for job in parse(data):
            seen.setdefault(job.id, job)
    return Fetch(list(seen.values()))
