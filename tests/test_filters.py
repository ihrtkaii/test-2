import pytest

from parttime import filters, models
from parttime.filters import Config
from parttime.models import Job

CFG = Config(local=filters.DEFAULT_LOCAL, priority=("l3harris", "ucf", "university of central florida"))


def job(title, location="Remote - US", etype=models.UNKNOWN, desc="", source="lever",
        remote=None, hybrid=None, company="Acme"):
    return Job(id=f"t:{title}", source=source, company=company, title=title, location=location,
               url="https://x", board="t", employment_type=etype, description=desc,
               remote=remote, hybrid=hybrid)


def decide(*a, **k):
    j = job(*a, **k)
    return filters.classify(j, CFG), j


# ---------------------------------------------------------------- security titles
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


# ---------------------------------------------------------------- seniority (review item 6)
@pytest.mark.parametrize("title", [
    "Senior SOC Analyst (Part-Time)", "Security Manager", "Lead Pen Tester",
    "Security Analyst III", "Principal Security Engineer",
])
def test_senior_blocked(title):
    d, _ = decide(title, etype=models.PART_TIME)
    assert d.reasons == ["senior"]


@pytest.mark.parametrize("title", [
    "Identity Management Intern - Part Time", "Threat Intelligence Intern (Part-Time)",
    "Cybersecurity Leadership Development Intern, Part-Time", "Security Analyst II (Part-Time)",
])
def test_not_senior(title):
    assert not filters.is_senior(title)


def test_senior_words_in_description_do_not_count():
    d, _ = decide("Part-Time SOC Analyst",
                  desc="You will support senior analysts and the team lead. 15 hours per week.")
    assert d.verdict == models.LISTED


# ---------------------------------------------------------------- hours parsing
@pytest.mark.parametrize("text,expected", [
    ("Work 15-20 hours per week", (15, 20)),
    ("up to 20 hours a week", (None, 20)),
    ("approximately 16 hrs/week", (16, 16)),
    ("20 hours weekly", (20, 20)),
    ("at least 30 hours per week", (30, None)),
    ("no schedule given", (None, None)),
    ("Up to 40 hours per week in the summer; 15 hours per week during the semester", (15, 15)),
])
def test_parse_hours(text, expected):
    assert filters.parse_hours(text) == expected


# ---------------------------------------------------------------- schedule levels (review item 1)
def test_confirmed_fit():
    d, j = decide("Security Analyst", desc="This role is 15-20 hours per week.")
    assert d.verdict == models.LISTED and j.schedule_fit == models.FIT_CONFIRMED
    assert j.hours == "15–20/wk" and j.evidence["hours"] == "This role is 15-20 hours per week."


def test_part_time_without_hours_is_possible():
    d, j = decide("Cybersecurity Intern", desc="This is a part-time internship.")
    assert d.verdict == models.LISTED and j.schedule_fit == models.FIT_POSSIBLE
    assert "Hours unverified" in j.flags and "weekly hours" in d.missing


def test_range_crossing_20_is_possible():
    d, j = decide("SOC Analyst", etype=models.PART_TIME, desc="10-25 hours per week.")
    assert j.schedule_fit == models.FIT_POSSIBLE and "Hours may exceed 20" in j.flags


def test_minimum_25_rejected():
    d, _ = decide("SOC Analyst", etype=models.PART_TIME, desc="Minimum 25 hours per week.")
    assert d.reasons == ["hours-over-limit"]


def test_fall_internship_goes_to_review():
    d, j = decide("Cybersecurity Intern - Fall 2026")
    assert d.verdict == models.REVIEW and j.schedule_fit == models.FIT_UNKNOWN
    assert d.reasons == ["schedule-unknown"]


def test_flexible_but_40_hours_rejected():
    d, _ = decide("Security Analyst", desc="Flexible hours, 40 hours per week.")
    assert d.reasons == ["hours-over-limit"]


def test_semester_hours_with_summer_commitment():
    d, j = decide("Cybersecurity Intern",
                  desc="Interns work 20 hours during semesters; 40 during summer.")
    assert d.verdict == models.LISTED and j.schedule_fit == models.FIT_CONFIRMED
    assert j.summer_commitment and "Full-time in summer" in j.flags


def test_full_time_student_wording_is_not_full_time_job():
    d, j = decide("Cybersecurity Intern - Spring",
                  desc="Must be enrolled as a full-time student. Part-time role, 12 hours per week. "
                       "Interns may convert to full-time after graduation.")
    assert d.verdict == models.LISTED and j.hours == "12/wk"


def test_full_time_job_rejected():
    d, _ = decide("SOC Analyst", etype=models.FULL_TIME)
    assert d.reasons == ["full-time"]
    d, _ = decide("Cybersecurity Co-op - Fall", desc="This is a full-time co-op position.")
    assert d.reasons == ["full-time"]


def test_summer_only_internship_rejected():
    d, _ = decide("Cybersecurity Intern - Summer 2027")
    assert d.reasons == ["summer-only"]


def test_summer_title_that_continues_part_time():
    d, j = decide("Cybersecurity Intern - Summer 2027",
                  desc="Strong interns may continue part-time into the fall semester.")
    assert d.verdict == models.LISTED and j.schedule_fit == models.FIT_POSSIBLE


def test_unstated_internship_rejected():
    d, _ = decide("Cybersecurity Intern")
    assert d.reasons == ["not-part-time"]


def test_full_time_contract_rejected():
    d, _ = decide("Cybersecurity Analyst (Contract)", etype=models.CONTRACT,
                  desc="12-month contract, full-time, 40 hours per week")
    assert d.reasons == ["hours-over-limit"]


def test_freelance_contract_goes_to_review():
    d, j = decide("Freelance Penetration Tester", etype=models.CONTRACT,
                  desc="Project-based work, set your own schedule. $45-$70 per hour.")
    assert d.verdict == models.REVIEW
    assert j.kind == models.KIND_CONTRACT and j.salary == "$45–$70/hr"


def test_freelance_with_hours_is_listed():
    d, j = decide("Freelance Penetration Tester", etype=models.CONTRACT,
                  desc="Project-based, about 10 hours per week.")
    assert d.verdict == models.LISTED and j.kind == models.KIND_CONTRACT


# ---------------------------------------------------------------- experience (review item 6)
def test_required_5_years_rejected_with_sentence():
    d, _ = decide("Part-Time Security Analyst",
                  desc="Requires 5 years of professional security experience. Remote.")
    assert d.reasons == ["experience-required"]
    assert d.evidence["experience"] == "Requires 5 years of professional security experience."


def test_preferred_5_years_flagged_not_rejected():
    d, j = decide("Part-Time Security Analyst", desc="5 years of experience preferred.")
    assert d.verdict == models.LISTED and "5+ yrs preferred" in j.flags


def test_equivalent_combination_flagged():
    d, j = decide("Part-Time Security Analyst",
                  desc="Bachelor's plus 2 years of experience, or equivalent combination of education.")
    assert d.verdict == models.LISTED and "2 yrs or equivalent" in j.flags


def test_framework_age_is_not_experience():
    d, j = decide("Part-Time Security Analyst",
                  desc="Familiarity with frameworks developed over the past 5 years.")
    assert d.verdict == models.LISTED and not any("yrs" in f for f in j.flags)


def test_two_years_required_flagged():
    d, j = decide("Part-Time Security Analyst", desc="2+ years experience in IT required.")
    assert d.verdict == models.LISTED and "2+ yrs exp" in j.flags


def test_clearance_flag():
    d, j = decide("Part-Time Cyber Analyst", desc="Must have an active Secret clearance.")
    assert d.verdict == models.LISTED and "Clearance" in j.flags


# ---------------------------------------------------------------- location (review item 3)
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
    ("Austin, TX (Hybrid)", None, None),
    ("Orlando, FL; Remote", None, "Remote / Orlando"),
    ("Remote - US; London", None, "Remote"),
])
def test_where(location, remote, expected):
    assert filters.where(job("x", location=location, remote=remote)) == expected


def test_florida_listed_in_eligible_states():
    d, j = decide("Part-Time SOC Analyst",
                  desc="Candidates must reside in one of: Florida, Georgia, Texas.")
    assert d.verdict == models.LISTED and j.florida_eligibility == "yes"


def test_florida_not_in_eligible_states():
    d, _ = decide("Part-Time SOC Analyst",
                  desc="You must reside in California, Oregon or Washington.")
    assert d.reasons == ["florida-ineligible"]
    assert "California" in d.evidence["location"]


def test_florida_excluded():
    d, _ = decide("Part-Time SOC Analyst",
                  desc="We are unable to hire in Florida, Alaska or Hawaii at this time.")
    assert d.reasons == ["florida-ineligible"]


def test_office_list_is_not_a_restriction():
    d, j = decide("Part-Time SOC Analyst",
                  desc="We have offices in London, Toronto and Austin, Texas. 15 hours per week.")
    assert d.verdict == models.LISTED and j.florida_eligibility == "likely"


def test_bare_remote_is_unverified():
    d, j = decide("Part-Time SOC Analyst", location="Remote")
    assert d.verdict == models.LISTED and "Location unverified" in j.flags
    assert j.florida_eligibility == "unknown"


def test_us_remote_is_likely():
    _, j = decide("Part-Time SOC Analyst", location="Remote - United States")
    assert j.florida_eligibility == "likely" and j.work_mode == "remote"


def test_out_of_area_onsite_dropped():
    d, _ = decide("Part-Time SOC Analyst", location="Denver, CO")
    assert d.reasons == ["location"]


# ---------------------------------------------------------------- broad titles (review item 2)
DUTIES = ("Monitor SIEM alerts in Splunk, support vulnerability scans with Nessus, "
          "and help with access reviews. 15 hours per week during the semester.")


def test_broad_title_with_security_duties_listed():
    d, j = decide("IT Student Assistant", location="Orlando, FL", desc=DUTIES)
    assert d.verdict == models.LISTED and "Security duties, broad title" in j.flags


def test_broad_title_help_desk_duties_rejected():
    d, _ = decide("IT Student Assistant", location="Orlando, FL",
                  desc="Answer help desk tickets, reset passwords and image laptops. "
                       "Basic security awareness. 15 hours per week.")
    assert d.reasons == ["duties-not-security"]


def test_help_desk_title_never_gated():
    assert filters.title_gate(job("Help Desk Student Assistant"), CFG) is None


def test_broad_title_needs_student_signal_or_priority():
    assert filters.title_gate(job("Technology Risk Analyst"), CFG) is None
    assert filters.title_gate(job("Technology Risk Intern"), CFG) == "ambiguous"
    assert filters.title_gate(job("Technology Risk Analyst", company="L3Harris"), CFG) == "ambiguous"


def test_priority_broad_title_without_description_goes_to_review():
    d, j = decide("Network Intern - Spring", company="L3Harris")
    assert d.verdict == models.REVIEW and "broad-title-no-description" in d.reasons
    assert j.priority


def test_non_priority_broad_title_without_description_rejected():
    d, _ = decide("Network Intern - Spring")
    assert d.reasons == ["no-duty-evidence"]


# ---------------------------------------------------------------- federal
def test_federal_student_trainee_with_cyber_duties():
    d, j = decide("Student Trainee (Information Technology)", location="Orlando, Florida",
                  source="usajobs", desc="Assists with cybersecurity monitoring and incident response.")
    assert d.verdict == models.REVIEW and j.kind == models.KIND_FEDERAL
    assert "Federal: check eligibility" in j.flags


def test_federal_generic_it_dropped():
    d, _ = decide("Student Trainee (Information Technology)", location="Orlando, Florida",
                  source="usajobs", desc="Help desk support and hardware imaging.")
    assert d.reasons == ["not-security"]


def test_federal_part_time_infosec():
    d, j = decide("IT Specialist (INFOSEC)", location="Anywhere in the U.S. (remote job)",
                  source="usajobs", etype=models.PART_TIME, desc="Requires 5 years of experience")
    assert d.verdict == models.LISTED  # experience gate does not apply to federal
    assert j.florida_eligibility == "likely"


# ---------------------------------------------------------------- first live run (2026-10-21)
@pytest.mark.parametrize("desc", [
    "Participate in an on-call rotation as needed, including nights and weekends.",
    "We provide a WFH budget for you to outfit your space as needed.",
    "Must be able to carry / move laptop as needed.",
    "We offer a flexible schedule and unlimited PTO.",
])
def test_as_needed_and_perks_are_not_a_schedule(desc):
    d, _ = decide("Security Software Engineer, IAM", desc=desc)
    assert d.reasons == ["not-part-time"]


@pytest.mark.parametrize("desc", [
    "Full or part-time enrollment in an accredited college or university.",
    "Open to students enrolled full-time or part-time.",
    "Must be a part-time or full-time student in good standing.",
])
def test_part_time_enrollment_is_not_a_part_time_job(desc):
    d, _ = decide("Cybersecurity Analyst Intern", desc=desc)
    assert d.reasons == ["not-part-time"]


def test_part_time_job_next_to_enrollment_wording():
    d, j = decide("Cybersecurity Analyst Intern",
                  desc="Must be enrolled full-time. This is a part-time role during the semester.")
    assert d.verdict == models.LISTED and "part-time role" in j.evidence["schedule"]


def test_summer_title_rejected_even_with_part_time_facet():
    d, _ = decide("Summer 2027 IT Audit Undergraduate Intern", etype=models.PART_TIME)
    assert d.reasons == ["summer-only"]
    d, _ = decide("Cybersecurity Intern - Summer 2027", desc="20 hours per week.")
    assert d.reasons == ["summer-only"]


@pytest.mark.parametrize("title", [
    "Security/Door Host", "Security & Door Staff", "Door Host", "Security Associate",
    "Security Coordinator",
])
def test_guard_and_bare_security_titles_fail(title):
    assert not filters.is_security(title)


def test_bare_security_with_cyber_word_still_passes():
    assert filters.is_security("IT Security Associate (Part-Time)")
    assert filters.is_security("Security Engineer Intern")


def test_bare_security_title_judged_by_duties():
    d, _ = decide("Security Coordinator - Part Time", location="Orlando, FL",
                  desc="Greet guests and patrol the venue.")
    assert d.reasons == ["duties-not-security"]


@pytest.mark.parametrize("location", [
    "Remote, Germany; Frankfurt, DEU", "Remote - Costa Rica", "Remote, DEU",
    "Remote - Canada; Toronto",
])
def test_foreign_remote_rejected(location):
    d, _ = decide("Part-Time SOC Analyst", location=location)
    assert d.reasons == ["location"]


@pytest.mark.parametrize("location", [
    "Remote, Remote, United States", "Riedman - Remote", "Denver, Colorado, United States; Remote - United States",
    "Remote - Canada; Remote - US",
])
def test_us_or_plain_remote_kept(location):
    _, j = decide("Part-Time SOC Analyst", location=location)
    assert j.work_mode == "remote"


def test_source_remote_with_office_city_kept():
    _, j = decide("Part-Time SOC Analyst", location="Austin", remote=True)
    assert j.work_mode == "remote"


def test_bare_security_title_with_security_duties_listed():
    d, _ = decide("Security Coordinator - Part Time", location="Orlando, FL",
                  desc="Monitor SIEM alerts, triage phishing reports, run vulnerability scans "
                       "and help with incident response.")
    assert d.verdict == models.LISTED
