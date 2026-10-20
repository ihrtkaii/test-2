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
    {"name": "UCF", "slug": "ucf", "ats": "workday", "wd": "wd1", "site": "careers"},
    {"name": "Broken Co", "slug": "broken", "ats": "lever"},
    {"name": "SR Co", "slug": "srco", "ats": "smartrecruiters"},
    {"name": "Skipped", "slug": "skip", "ats": "workable"},
]

GH_LIST = {"jobs": [
    {"id": 1, "title": "Part-Time SOC Analyst", "absolute_url": "https://gh/1",
     "location": {"name": "Remote - United States"}, "first_published": "2026-10-10T00:00:00Z"},
    {"id": 2, "title": "Account Executive", "absolute_url": "https://gh/2",
     "location": {"name": "Remote"}},
]}
GH_FULL = {"jobs": [
    {**GH_LIST["jobs"][0], "content": "&lt;p&gt;Part-time, 15-20 hours per week. $25-$30 per hour.&lt;/p&gt;"},
    {**GH_LIST["jobs"][1], "content": "sales"},
]}
LEVER = [
    {"id": "a", "text": "Cybersecurity Intern - Spring 2027", "hostedUrl": "https://lever/a",
     "createdAt": 1760000000000, "workplaceType": "hybrid",
     "categories": {"commitment": "Intern", "location": "Orlando, FL"},
     "descriptionPlain": "Spring semester internship.", "lists": []},
    {"id": "b", "text": "Security Engineer", "hostedUrl": "https://lever/b",
     "categories": {"commitment": "Full-time", "location": "Remote"}, "workplaceType": "remote"},
]
ASHBY = {"jobs": [
    {"title": "GRC Analyst", "employmentType": "PartTime", "isRemote": True,
     "workplaceType": "Remote", "location": "United States", "jobUrl": "https://ashby/x/abc",
     "publishedAt": "2026-10-01", "descriptionPlain": "Up to 20 hours a week. 2+ years experience.",
     "compensation": {"compensationTierSummary": "$35/hr"}},
    {"title": "GRC Analyst", "employmentType": "PartTime", "isRemote": False,
     "workplaceType": "OnSite", "location": "Berlin, Germany", "jobUrl": "https://ashby/x/def"},
]}
WD_FIRST = {"total": 2, "jobPostings": [], "facets": [
    {"facetParameter": "timeType", "descriptor": "Time Type", "values": [
        {"descriptor": "Full time", "id": "ft1", "count": 9},
        {"descriptor": "Part time", "id": "pt1", "count": 2}]}]}
WD_PT = {"total": 1, "jobPostings": [
    {"title": "Student Assistant - Cyber Defense", "externalPath": "/job/Orlando/Student-Assistant_R1",
     "locationsText": "Orlando, FL", "postedOn": "Posted Today"}]}
WD_INTERN = {"total": 0, "jobPostings": [], "facets": []}
WD_DETAIL = {"jobPostingInfo": {"title": "Student Assistant - Cyber Defense",
                                "jobDescription": "<p>Work 10-20 hours per week around classes.</p>",
                                "timeType": "Part time", "location": "Orlando, FL"}}
REMOTIVE = {"jobs": [
    {"id": 7, "url": "https://remotive/7", "title": "Freelance Pentester", "company_name": "Hackco",
     "job_type": "freelance", "candidate_required_location": "USA",
     "publication_date": "2026-10-15T00:00:00", "description": "Project-based, set your own hours."},
    {"id": 8, "url": "https://remotive/8", "title": "SOC Analyst", "company_name": "Euro",
     "job_type": "part_time", "candidate_required_location": "Europe", "description": ""},
]}


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "greenhouse.io" in url:
        return httpx.Response(200, json=GH_FULL if "content=true" in url else GH_LIST)
    if "lever.co" in url:
        return httpx.Response(500) if "broken" in url else httpx.Response(200, json=LEVER)
    if "ashbyhq.com" in url:
        return httpx.Response(200, json=ASHBY)
    if "myworkdayjobs.com" in url:
        if request.method == "GET":
            return httpx.Response(200, json=WD_DETAIL)
        body = json.loads(request.content)
        if body["appliedFacets"]:
            return httpx.Response(200, json=WD_PT)
        return httpx.Response(200, json=WD_INTERN if body["searchText"] == "intern" else WD_FIRST)
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
    monkeypatch.setattr(pipeline, "DATA", data)
    monkeypatch.setattr(pipeline, "DOCS", docs)
    monkeypatch.setattr(pipeline, "README", tmp_path / "README.md")
    monkeypatch.setattr(pipeline, "TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr("parttime.net._backoff", lambda *a, **k: 0)
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    return tmp_path


def open_jobs(root):
    return {j["title"]: j for j in json.loads((root / "docs" / "jobs.json").read_text())["jobs"]}


def test_full_run(sandbox):
    stats = pipeline.run()
    jobs = open_jobs(sandbox)
    assert set(jobs) == {"Part-Time SOC Analyst", "Cybersecurity Intern - Spring 2027",
                         "GRC Analyst", "Student Assistant - Cyber Defense",
                         "Freelance Pentester"}
    soc = jobs["Part-Time SOC Analyst"]
    assert soc["hours"] == "15–20/wk" and soc["salary"] == "$25–$30/hr"
    assert jobs["Cybersecurity Intern - Spring 2027"]["kind"] == models.KIND_INTERNSHIP
    assert jobs["Cybersecurity Intern - Spring 2027"]["where"] == "Orlando area (Hybrid)"
    grc = jobs["GRC Analyst"]
    assert grc["salary"] == "$35/hr" and "2+ yrs exp" in grc["flags"]
    ucf = jobs["Student Assistant - Cyber Defense"]
    assert ucf["kind"] == models.KIND_INTERNSHIP and ucf["hours"] == "10–20/wk"
    assert jobs["Freelance Pentester"]["kind"] == models.KIND_CONTRACT
    assert stats["boards_failed"]["lever"] == 1      # broken board isolated
    assert "usajobs" in stats["boards_failed"]        # no key: reported, not fatal
    readme = (sandbox / "README.md").read_text()
    assert "## Part-Time Jobs (2)" in readme and "Part-Time SOC Analyst" in readme
    assert "description" not in json.dumps(json.loads((sandbox / "data" / "jobs.json").read_text()))


def test_closing_and_failed_boards(sandbox, monkeypatch):
    pipeline.run()
    first = json.loads((sandbox / "data" / "jobs.json").read_text())
    # The greenhouse role disappears; Lever's board now errors.
    monkeypatch.setitem(GH_LIST, "jobs", [])
    monkeypatch.setitem(GH_FULL, "jobs", [])
    monkeypatch.setattr(pipeline, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(500) if "lever.co" in str(r.url) else handler(r)))
    pipeline.run()
    store = json.loads((sandbox / "data" / "jobs.json").read_text())
    assert store["greenhouse:acmesec:1"]["status"] == "closed"
    assert store["lever:leverco:a"]["status"] == "open"   # failed read never closes
    assert store["ashby:ashbyco:abc"]["first_seen"] == first["ashby:ashbyco:abc"]["first_seen"]


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
        "UserArea": {"Details": {"JobSummary": "Protects networks.", "RemoteIndicator": False}},
    }}]}}
    [job] = usajobs.parse(payload)
    assert job.employment_type == models.PART_TIME
    assert job.salary == "$22.50–$29.10/hr"
    assert filters.classify(job) is None and job.kind == models.KIND_FEDERAL
    assert job.where == "Orlando area"
