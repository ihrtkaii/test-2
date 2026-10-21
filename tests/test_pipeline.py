"""End to end against a mock network. Payload shapes follow each provider's
public API so a parsing mistake shows up here, not in the first live run."""

import json

import httpx
import pytest

from parttime import models, pipeline

COMPANIES = [
    {"name": "Acme Sec", "slug": "acmesec", "ats": "greenhouse"},
    {"name": "Lever Co", "slug": "leverco", "ats": "lever"},
    {"name": "Ashby Co", "slug": "ashbyco", "ats": "ashby"},
    {"name": "University of Central Florida", "slug": "ucf", "ats": "workday", "wd": "wd1",
     "site": "careers"},
    {"name": "Broken Co", "slug": "broken", "ats": "lever"},
    {"name": "SR Co", "slug": "srco", "ats": "smartrecruiters"},
    {"name": "Skipped", "slug": "skip", "ats": "workable"},
]

GH_JOBS = [
    {"id": 1, "title": "Part-Time SOC Analyst", "absolute_url": "https://gh/1",
     "location": {"name": "Remote - United States"}, "first_published": "2026-10-10T00:00:00Z",
     "updated_at": "2026-10-11T00:00:00Z", "requisition_id": "REQ-77"},
    {"id": 2, "title": "Account Executive", "absolute_url": "https://gh/2",
     "location": {"name": "Remote"}, "updated_at": "2026-10-11T00:00:00Z"},
]
GH_CONTENT = {1: "&lt;p&gt;Part-time, 15-20 hours per week. $25-$30 per hour.&lt;/p&gt;",
              2: "sales"}
LEVER = [
    {"id": "a", "text": "Cybersecurity Intern - Spring 2027", "hostedUrl": "https://lever/a",
     "createdAt": 1760000000000, "workplaceType": "hybrid",
     "categories": {"commitment": "Intern", "location": "Orlando, FL"},
     "descriptionPlain": "Spring semester internship, 10-15 hours per week.", "lists": []},
    {"id": "b", "text": "Security Engineer", "hostedUrl": "https://lever/b",
     "categories": {"commitment": "Full-time", "location": "Remote"}, "workplaceType": "remote"},
    {"id": "c", "text": "Cybersecurity Intern - Fall 2026", "hostedUrl": "https://lever/c",
     "categories": {"commitment": "Intern", "location": "Remote - US"},
     "descriptionPlain": "Join our fall cohort.", "lists": []},
]
ASHBY = {"jobs": [
    {"title": "GRC Analyst", "employmentType": "PartTime", "isRemote": True,
     "workplaceType": "Remote", "location": "United States", "jobUrl": "https://ashby/x/abc",
     "publishedAt": "2026-10-01", "descriptionPlain": "Up to 20 hours a week. 2+ years experience.",
     "compensation": {"compensationTierSummary": "$35/hr"}},
    {"title": "GRC Analyst", "employmentType": "PartTime", "isRemote": False,
     "workplaceType": "OnSite", "location": "Berlin, Germany", "jobUrl": "https://ashby/x/def"},
]}
WD_PROBE = {"total": 0, "jobPostings": [], "facets": [
    {"facetParameter": "timeType", "descriptor": "Time Type", "values": [
        {"descriptor": "Full time", "id": "ft1", "count": 9},
        {"descriptor": "Part time", "id": "pt1", "count": 2}]}]}
WD_PT = {"total": 1, "jobPostings": [
    {"title": "Student Assistant - Cyber Defense", "externalPath": "/job/Orlando/Student-Assistant_R1",
     "locationsText": "Orlando, FL", "postedOn": "Posted Today"}]}
WD_STUDENT = {"total": 1, "jobPostings": [
    {"title": "IT Student Assistant", "externalPath": "/job/Orlando/IT-Student_R2",
     "locationsText": "Orlando, FL", "postedOn": "Posted Today"}]}
WD_EMPTY = {"total": 0, "jobPostings": []}
WD_DETAIL = {
    "/job/Orlando/Student-Assistant_R1": {"jobPostingInfo": {
        "jobDescription": "<p>Work 10-20 hours per week around classes.</p>",
        "timeType": "Part time", "location": "Orlando, FL", "jobReqId": "R1"}},
    "/job/Orlando/IT-Student_R2": {"jobPostingInfo": {
        "jobDescription": "<p>Monitor SIEM alerts in Splunk, run Nessus vulnerability scans and "
                          "help with user access reviews. 15 hours per week during the semester.</p>",
        "timeType": "Part time", "location": "Orlando, FL", "jobReqId": "R2"}},
}
REMOTIVE = {"jobs": [
    {"id": 7, "url": "https://remotive/7", "title": "Freelance Pentester", "company_name": "Hackco",
     "job_type": "freelance", "candidate_required_location": "USA",
     "publication_date": "2026-10-15T00:00:00", "description": "Project-based, about 10 hours per week."},
    {"id": 8, "url": "https://remotive/8", "title": "SOC Analyst", "company_name": "Euro",
     "job_type": "part_time", "candidate_required_location": "Europe", "description": ""},
    # A repost of Acme's role: must merge into the Greenhouse record.
    {"id": 9, "url": "https://gh/1?utm_source=remotive", "title": "Part-Time SOC Analyst",
     "company_name": "Acme Sec", "job_type": "part_time",
     "candidate_required_location": "USA", "description": "Part-time, 15-20 hours per week."},
]}

CALLS = []


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    CALLS.append(url)
    if "greenhouse.io" in url:
        content = "content=true" in url
        jobs = [{**j, **({"content": GH_CONTENT[j["id"]]} if content else {})} for j in GH_JOBS]
        return httpx.Response(200, json={"jobs": jobs})
    if "lever.co" in url:
        return httpx.Response(500) if "broken" in url else httpx.Response(200, json=LEVER)
    if "ashbyhq.com" in url:
        return httpx.Response(200, json=ASHBY)
    if "myworkdayjobs.com" in url:
        if request.method == "GET":
            path = url.split("/wday/cxs/ucf/careers", 1)[1]
            return httpx.Response(200, json=WD_DETAIL[path])
        body = json.loads(request.content)
        if body["appliedFacets"]:
            return httpx.Response(200, json=WD_PT if body["searchText"] == "cyber" else WD_EMPTY)
        if body["searchText"] == "student":
            return httpx.Response(200, json=WD_STUDENT)
        return httpx.Response(200, json=WD_PROBE if body["offset"] == 0 else WD_EMPTY)
    if "smartrecruiters.com" in url:
        return httpx.Response(200, json={"content": [], "totalFound": 0})
    if "remotive.com" in url:
        return httpx.Response(200, json=REMOTIVE)
    if "jobicy.com" in url:
        return httpx.Response(200, json={"jobs": []})
    if "remoteok.com" in url:
        return httpx.Response(200, json=[{"legal": "notice"}])
    return httpx.Response(404)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    data, docs = tmp_path / "data", tmp_path / "docs"
    data.mkdir()
    (data / "companies.json").write_text(json.dumps(COMPANIES))
    (data / "config.json").write_text(json.dumps({"priority_employers": ["ucf",
                                                  "university of central florida"]}))
    monkeypatch.setattr(pipeline, "DATA", data)
    monkeypatch.setattr(pipeline, "DOCS", docs)
    monkeypatch.setattr(pipeline, "README", tmp_path / "README.md")
    monkeypatch.setattr(pipeline, "CACHE", data / "cache" / "d.json.gz")
    monkeypatch.setattr(pipeline, "TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr("parttime.net._backoff", lambda *a, **k: 0)
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    CALLS.clear()
    return tmp_path


def rows(root):
    return {j["title"]: j for j in json.loads((root / "docs" / "jobs.json").read_text())["jobs"]}


def test_full_run(sandbox):
    stats = pipeline.run()
    jobs = rows(sandbox)
    assert set(jobs) == {"Part-Time SOC Analyst", "Cybersecurity Intern - Spring 2027",
                         "Cybersecurity Intern - Fall 2026", "GRC Analyst",
                         "Student Assistant - Cyber Defense", "IT Student Assistant",
                         "Freelance Pentester"}
    soc = jobs["Part-Time SOC Analyst"]
    assert soc["hours"] == "15–20/wk" and soc["salary"] == "$25–$30/hr"
    assert soc["schedule_fit"] == models.FIT_CONFIRMED
    # Remotive repost merged into the Greenhouse record, provenance kept.
    assert soc["primary_source"] == "greenhouse"
    assert [a["source"] for a in soc["alternate_sources"]] == ["remotive"]
    spring = jobs["Cybersecurity Intern - Spring 2027"]
    assert spring["kind"] == models.KIND_INTERNSHIP and spring["where"] == "Orlando area (Hybrid)"
    fall = jobs["Cybersecurity Intern - Fall 2026"]
    assert fall["queue"] == models.REVIEW and fall["schedule_fit"] == models.FIT_UNKNOWN
    grc = jobs["GRC Analyst"]
    assert grc["salary"] == "$35/hr" and "2+ yrs exp" in grc["flags"]
    # Workday: facet pass found the cyber role; priority student pass found
    # the broad-title role, which passed on its duties.
    assert jobs["Student Assistant - Cyber Defense"]["hours"] == "10–20/wk"
    it = jobs["IT Student Assistant"]
    assert it["priority"] and "Security duties, broad title" in it["flags"]
    assert stats["workday_outcomes"]["facet-found"] == 1
    assert stats["boards"]["lever"]["failed"] == 1        # broken board isolated
    assert stats["boards"]["usajobs"]["failed"] == 1      # no key: reported, not fatal
    assert stats["listed"] == 6 and stats["review"] == 1
    readme = (sandbox / "README.md").read_text()
    assert "Part-Time SOC Analyst" in readme and "Cybersecurity Intern - Fall 2026" not in readme
    store = (sandbox / "data" / "jobs.json").read_text()
    assert "Part-time, 15-20 hours per week. $25" not in store   # no descriptions stored
    decisions = [json.loads(x) for x in (sandbox / "data" / "decisions.jsonl").read_text().splitlines()]
    assert any(d["decision"] == "rejected" and d["title"] == "Security Engineer" for d in decisions)


def test_cache_skips_second_download(sandbox):
    pipeline.run()
    first = sum("content=true" in c for c in CALLS)
    wd_detail = sum("/job/Orlando/" in c for c in CALLS)
    CALLS.clear()
    stats = pipeline.run()
    assert first == 1 and wd_detail == 2
    assert not any("content=true" in c for c in CALLS)
    assert not any("/job/Orlando/" in c for c in CALLS)
    assert stats["cache"]["hits"] >= 3


def _rerun_without(sandbox, monkeypatch, *, hours_later: float):
    """Run again with Acme's role removed and the Lever board broken."""
    from datetime import timedelta
    real_now = pipeline._now
    base = pipeline.datetime.now(pipeline.UTC) + timedelta(hours=hours_later)
    monkeypatch.setattr(pipeline, "_now", lambda: base.strftime("%Y-%m-%dT%H:%M:%SZ"))
    monkeypatch.setattr(pipeline, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(500) if "lever.co" in str(r.url)
        else httpx.Response(200, json={"jobs": []}) if "greenhouse.io" in str(r.url)
        else httpx.Response(200, json={"jobs": [REMOTIVE["jobs"][0]]}) if "remotive.com" in str(r.url)
        else handler(r)))
    pipeline.run()
    monkeypatch.setattr(pipeline, "_now", real_now)
    return json.loads((sandbox / "data" / "jobs.json").read_text())


def test_closing_needs_two_complete_scans_12h_apart(sandbox, monkeypatch):
    pipeline.run()
    first = json.loads((sandbox / "data" / "jobs.json").read_text())
    key = "greenhouse:acmesec:1"
    assert first[key]["status"] == "open"
    s1 = _rerun_without(sandbox, monkeypatch, hours_later=1)
    assert s1[key]["status"] == "missing"
    s2 = _rerun_without(sandbox, monkeypatch, hours_later=6)
    assert s2[key]["status"] == "missing"          # two scans, but only 5h apart
    s3 = _rerun_without(sandbox, monkeypatch, hours_later=14)
    assert s3[key]["status"] == "closed"
    # Lever's board errored every time: its roles never moved.
    assert s3["lever:leverco:a"]["status"] == "open"
    assert s3["lever:leverco:a"]["last_seen"] == first["lever:leverco:a"]["last_seen"]
    assert s3["ashby:ashbyco:abc"]["first_seen"] == first["ashby:ashbyco:abc"]["first_seen"]


def test_scope_change_blocks_closing(sandbox, monkeypatch):
    pipeline.run()
    (sandbox / "data" / "config.json").write_text(json.dumps({"priority_employers": ["ucf"]}))
    s1 = _rerun_without(sandbox, monkeypatch, hours_later=1)
    assert s1["greenhouse:acmesec:1"]["status"] == "open"
    stats = json.loads((sandbox / "data" / "stats.json").read_text())
    assert stats["closing_allowed"] is False


def test_usajobs_parse():
    from parttime import filters
    from parttime.sources import usajobs
    payload = {"SearchResult": {"SearchResultItems": [{"MatchedObjectDescriptor": {
        "PositionID": "ABC-123", "PositionTitle": "IT Specialist (INFOSEC)",
        "PositionURI": "https://www.usajobs.gov/job/1", "OrganizationName": "Naval Air Warfare Center",
        "PositionLocationDisplay": "Orlando, Florida",
        "PositionSchedule": [{"Name": "Part-time", "Code": "2"}],
        "PositionRemuneration": [{"MinimumRange": "22.50", "MaximumRange": "29.10",
                                  "RateIntervalCode": "PH"}],
        "PublicationStartDate": "2026-10-05T00:00:00",
        "UserArea": {"Details": {"JobSummary": "Protects networks.", "RemoteIndicator": False,
                                 "HiringPath": ["student", "public"]}},
    }}]}}
    [job] = usajobs.parse(payload)
    assert job.employment_type == models.PART_TIME and job.hiring_paths == ["student", "public"]
    assert job.salary == "$22.50–$29.10/hr"
    d = filters.classify(job)
    assert d.verdict == models.LISTED and job.kind == models.KIND_FEDERAL
    assert job.where == "Orlando area"
