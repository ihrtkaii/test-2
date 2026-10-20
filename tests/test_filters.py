import pytest

from parttime import filters, models
from parttime.models import Job


def job(title, location="Remote - US", etype=models.UNKNOWN, desc="", source="lever",
        remote=None, hybrid=None):
    return Job(id=f"t:{title}", source=source, company="Acme", title=title, location=location,
               url="https://x", board="t", employment_type=etype, description=desc,
               remote=remote, hybrid=hybrid)


# ---------------------------------------------------------------- security gate
@pytest.mark.parametrize("title", [
    "Part-Time SOC Analyst", "Cybersecurity Intern (Fall 2026)", "GRC Analyst - Part Time",
    "Information Security Student Assistant", "Penetration Tester (Contract, Hourly)",
    "IT Specialist (INFOSEC)", "Security Operations Center Analyst I",
    "Network Security Intern - Spring", "Software Engineer, Security (Part-Time)",
])
def test_security_titles_pass(title):
    assert filters.is_security(title)


@pytest.mark.parametrize("title", [
    "Security Officer - Part Time", "Unarmed Security Guard", "Event Security Staff",
    "Loss Prevention Associate", "Software Engineer (Security Clearance Required)",
    "Securities Operations Analyst", "Cybersecurity Sales Development Rep",
    "Security Officer - Data Center", "Social Security Claims Specialist",
    "Adjunct Instructor - Cybersecurity",
])
def test_non_security_titles_fail(title):
    assert not filters.is_security(title)


def test_cyber_officer_survives_physical_veto():
    assert filters.is_security("Information Security Officer Intern")


@pytest.mark.parametrize("title", [
    "Senior SOC Analyst (Part-Time)", "Security Manager", "Lead Pen Tester",
    "Security Analyst III", "Principal Security Engineer",
])
def test_senior_blocked(title):
    assert filters.classify(job(title, etype=models.PART_TIME)) == "senior"


# ---------------------------------------------------------------- hours
@pytest.mark.parametrize("text,expected", [
    ("Work 15-20 hours per week", (15, 20)),
    ("up to 20 hours a week", (None, 20)),
    ("approximately 16 hrs/week", (16, 16)),
    ("20 hours weekly", (20, 20)),
    ("at least 30 hours per week", (30, None)),
    ("no schedule given", (None, None)),
])
def test_parse_hours(text, expected):
    assert filters.parse_hours(text) == expected


# ---------------------------------------------------------------- kinds
def test_part_time_job_from_employment_type():
    j = job("SOC Analyst", etype=models.PART_TIME)
    assert filters.classify(j) is None
    assert j.kind == models.KIND_JOB
    assert j.where == "Remote"


def test_part_time_from_description():
    j = job("Security Analyst", desc="This is a part-time role, 20 hours per week.")
    assert filters.classify(j) is None and j.hours == "20/wk"


def test_full_time_job_dropped():
    assert filters.classify(job("SOC Analyst", etype=models.FULL_TIME)) == "not-part-time"
    assert filters.classify(job("SOC Analyst", desc="Full-time, 40 hours per week")) \
        == "hours"


def test_part_time_over_20_hours_dropped():
    j = job("SOC Analyst", etype=models.PART_TIME, desc="Part-time, 30 hours per week.")
    assert filters.classify(j) == "hours"


def test_overlapping_hours_flagged():
    j = job("SOC Analyst", etype=models.PART_TIME, desc="Part-time, 15-25 hours per week.")
    assert filters.classify(j) is None
    assert "Hours may exceed 20" in j.flags


def test_fall_internship_kept():
    j = job("Cybersecurity Intern - Fall 2026")
    assert filters.classify(j) is None and j.kind == models.KIND_INTERNSHIP


def test_year_round_internship_kept():
    assert filters.classify(job("Year-Round Cyber Intern")) is None


def test_school_year_from_description():
    j = job("Information Security Intern",
            desc="Interns work flexible hours around their class schedule during the academic year.")
    assert filters.classify(j) is None


def test_summer_only_internship_dropped():
    assert filters.classify(job("Cybersecurity Intern - Summer 2027")) == "summer-only"


def test_unstated_internship_dropped():
    assert filters.classify(job("Cybersecurity Intern")) == "not-part-time"


def test_part_time_summer_kept():
    assert filters.classify(job("Part-Time Cybersecurity Intern (Summer/Fall)")) is None


def test_full_time_contract_dropped():
    j = job("Cybersecurity Analyst (Contract)", etype=models.CONTRACT,
            desc="12-month contract, full-time, 40 hours per week")
    assert filters.classify(j) == "hours"


def test_freelance_contract_kept():
    j = job("Freelance Penetration Tester", etype=models.CONTRACT,
            desc="Project-based work, set your own schedule. $45-$70 per hour.")
    assert filters.classify(j) is None
    assert j.kind == models.KIND_CONTRACT and j.salary == "$45–$70/hr"


def test_experience_gate():
    j = job("Part-Time Security Analyst", desc="Requires 5+ years of experience in security.")
    assert filters.classify(j) == "experience"
    j = job("Part-Time Security Analyst", desc="2+ years experience preferred.")
    assert filters.classify(j) is None and "2+ yrs exp" in j.flags


def test_clearance_flag():
    j = job("Part-Time Cyber Analyst", desc="Must have an active Secret clearance.")
    assert filters.classify(j) is None and "Clearance" in j.flags


# ---------------------------------------------------------------- location
@pytest.mark.parametrize("location,remote,expected", [
    ("Orlando, FL", None, "Orlando area"),
    ("Lake Mary, Florida", None, "Orlando area"),
    ("Kissimmee, FL (Hybrid)", None, "Orlando area (Hybrid)"),
    ("Remote", None, "Remote"),
    ("United States (Remote)", None, "Remote"),
    ("—", True, "Remote"),
    ("Remote - Canada", None, None),
    ("Remote (Europe)", None, None),
    ("Austin, TX", None, None),
    ("Orlando, FL; Remote", None, "Remote / Orlando"),
    ("Remote - US; London", None, "Remote"),
])
def test_where(location, remote, expected):
    assert filters.where(job("x", location=location, remote=remote)) == expected


def test_out_of_area_onsite_dropped():
    assert filters.classify(job("Part-Time SOC Analyst", location="Denver, CO")) == "location"


# ---------------------------------------------------------------- federal
def test_federal_student_trainee_with_cyber_duties():
    j = job("Student Trainee (Information Technology)", location="Orlando, Florida",
            source="usajobs", desc="Assists with cybersecurity monitoring and incident response.")
    assert filters.classify(j) is None and j.kind == models.KIND_FEDERAL


def test_federal_generic_it_dropped():
    j = job("Student Trainee (Information Technology)", location="Orlando, Florida",
            source="usajobs", desc="Help desk support and hardware imaging.")
    assert filters.classify(j) == "not-security"


def test_federal_part_time_infosec():
    j = job("IT Specialist (INFOSEC)", location="Anywhere in the U.S. (remote job)",
            source="usajobs", etype=models.PART_TIME, desc="Requires 5 years of experience")
    assert filters.classify(j) is None  # experience gate does not apply to federal
