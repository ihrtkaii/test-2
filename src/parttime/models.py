"""Data shapes shared by every source.

A source returns raw `Job` records with whatever it knows. The filter stage
decides what kind of role it is, how sure we are that the schedule fits, and
whether it goes on the main list, the review queue, or nowhere.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

CLASSIFIER_VERSION = "2.0"

# Normalized employment types. Sources map their own vocabulary onto these.
PART_TIME = "part-time"
FULL_TIME = "full-time"
INTERN = "intern"
CONTRACT = "contract"
TEMPORARY = "temporary"
UNKNOWN = "unknown"

# Final buckets on the list.
KIND_INTERNSHIP = "Part-Time Internships"
KIND_JOB = "Part-Time Jobs"
KIND_FEDERAL = "Federal / Pathways"
KIND_CONTRACT = "Contract / Freelance"
KINDS = (KIND_INTERNSHIP, KIND_JOB, KIND_FEDERAL, KIND_CONTRACT)

# How sure we are the weekly schedule fits.
FIT_CONFIRMED = "confirmed"   # stated weekly hours entirely at or under 20
FIT_POSSIBLE = "possible"     # explicitly part-time, or a range that crosses 20
FIT_UNKNOWN = "unknown"       # semester/seasonal/flexible wording, no hours evidence

# Where a role lands.
LISTED = "listed"     # main list
REVIEW = "review"     # worth checking by hand
REJECTED = "rejected"

# Lifecycle.
OPEN = "open"
MISSING = "missing"   # absent from one complete scan, not yet confirmed closed
CLOSED = "closed"
STALE = "stale"       # its source keeps failing, so it cannot be re-checked
FILTERED = "filtered"  # still posted, but no longer passes the rules


@dataclass
class Job:
    id: str                       # "<source>:<board>:<external id>", stable across runs
    source: str
    company: str
    title: str
    location: str
    url: str
    board: str = ""               # which board answered; closure is decided per board
    posted_at: str | None = None
    employment_type: str = UNKNOWN
    remote: bool | None = None    # True only when the source says so explicitly
    hybrid: bool | None = None
    salary: str | None = None
    requisition_id: str | None = None
    updated_sig: str | None = None  # source's change marker, used by the description cache
    hiring_paths: list[str] | None = None  # USAJOBS only
    description: str | None = None  # transient: never written to the store
    # Filled by the filter stage
    queue: str = ""               # LISTED | REVIEW
    kind: str = ""
    category: str = ""
    schedule_fit: str = ""
    hours_min: int | None = None
    hours_max: int | None = None
    hours: str | None = None      # display form of the two above
    summer_commitment: bool = False
    work_mode: str = "unknown"    # remote | hybrid | onsite | unknown
    florida_eligibility: str = "unknown"  # yes | likely | no | unknown
    where: str = ""               # "Remote" | "Orlando area" | ...
    priority: bool = False
    flags: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)

    def record(self) -> dict:
        data = asdict(self)
        data.pop("description", None)
        data.pop("updated_sig", None)
        return data


@dataclass
class Fetch:
    """One board's result.

    `ok` False: the board errored. Nothing it holds may change state.
    `complete` False: it answered, but we can't prove we saw every matching
    role (a capped page, a failed page, a recent-only feed). Its roles can be
    refreshed but never closed from this read.
    """

    jobs: list[Job]
    ok: bool = True
    complete: bool = True
    error: str | None = None
    notes: list[str] = field(default_factory=list)  # e.g. Workday outcome codes


@dataclass
class Decision:
    verdict: str                  # LISTED | REVIEW | REJECTED
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
