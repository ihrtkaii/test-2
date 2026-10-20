"""Data shapes shared by every source.

A source returns raw `Job` records with whatever it knows. The filter stage
decides what kind of role it is (part-time job, part-time internship, federal,
contract) and whether it belongs on the list at all.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

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


@dataclass
class Job:
    id: str                       # "<source>:<board>:<external id>", stable across runs
    source: str
    company: str
    title: str
    location: str
    url: str
    board: str = ""               # "<source>:<slug>", used to close roles safely
    posted_at: str | None = None
    employment_type: str = UNKNOWN
    remote: bool | None = None    # True when the source says so explicitly
    hybrid: bool | None = None
    salary: str | None = None
    description: str | None = None  # transient: never written to disk
    # Filled by the filter stage
    kind: str = ""
    category: str = ""
    hours: str | None = None
    flags: list[str] = field(default_factory=list)
    where: str = ""               # "Remote" | "Orlando area" | "Remote / Orlando"

    def record(self) -> dict:
        data = asdict(self)
        data.pop("description", None)
        return data


@dataclass
class Fetch:
    """One board's result. `ok` False means the board errored: its stored roles
    stay open instead of being closed on a bad read."""

    jobs: list[Job]
    ok: bool = True
    error: str | None = None
