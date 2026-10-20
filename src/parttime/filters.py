"""What belongs on the list.

A role makes the list when all four gates pass:

1. It is a security role (title), not physical security and not a senior post.
2. It is part-time, a fall/spring/year-round internship, federal student work,
   or hourly contract work. "Part-time" must be evidenced by the source's own
   employment type or by the posting text. A full-time 40-hour role never
   passes, and a summer-only internship is left to secnet-internships.
3. Stated weekly hours, when there are any, start at 20 or below.
4. It is remote (US-eligible) or in the Orlando area.

Everything here is pure functions over strings so it can be tested offline.
"""

from __future__ import annotations

import re

from . import models
from .models import Job

# ---------------------------------------------------------------------------
# 1. Security relevance. The title list matches secnet-internships, minus fraud
#    and trust & safety, which are mostly moderation and call-center work at
#    part-time hours.
# ---------------------------------------------------------------------------
SECURITY_RE = re.compile(
    r"\b("
    r"cyber|cybersecurity|infosec|appsec|"
    r"information security|application security|product security|"
    r"cloud security|network security|offensive security|security"
    r"(?:\s+(?:engineer|engineering|analyst|operations|architect|"
    r"research|researcher|intern|internship|consultant|specialist))?|"
    r"soc\s+analyst|security operations|"
    r"threat\s+(?:intel|intelligence|hunting|detection|research)|"
    r"detection\s+(?:engineer|engineering)|incident response|"
    r"vulnerability\s+(?:management|research|assessment)|"
    r"penetration\s+test(?:ing|er)?|pen\s?test(?:ing|er)?|red team|blue team|"
    r"bug bounty|malware|forensics|digital forensics|dfir|"
    r"identity\s+and\s+access|iam\b|zero trust|"
    r"grc\b|governance,?\s+risk|risk and compliance|it\s+audit|"
    r"compliance\s+(?:analyst|engineer)|nist|fedramp|cmmc|"
    r"information assurance"
    r")\b",
    re.IGNORECASE,
)

# Part-time "security" is mostly guard work. These titles mean a uniform, not
# a SIEM, unless the title also names a cyber/IT term.
PHYSICAL_RE = re.compile(
    r"\b("
    r"security\s+(?:officer|guard|patrol|agent|associate|supervisor|"
    r"screener|dispatcher|host|ambassador|attendant|monitor\w*)|"
    r"(?:armed|unarmed|event|campus|site|mobile|retail|hospital|flex)\s+security|"
    r"loss prevention|asset protection|public safety|police|"
    r"guard\b|bouncer|crowd management|concierge|doorman|"
    r"social security|security clearance|securities"
    r")\b",
    re.IGNORECASE,
)
CYBER_ANCHOR_RE = re.compile(
    r"\b(cyber\w*|information|infosec|it\b|network|cloud|soc\b|siem|"
    r"application|digital)\b",
    re.IGNORECASE,
)

FED_CYBER_RE = re.compile(
    r"\b(cyber\w*|information security|infosec|information assurance|"
    r"incident response|vulnerabilit\w+|security operations)\b",
    re.IGNORECASE,
)

SENIOR_RE = re.compile(
    r"\b(senior|sr\.?|staff|principal|manager|director|lead|vp|head|chief|"
    r"architect|iii|iv|level\s*[3-5])\b",
    re.IGNORECASE,
)

NON_TECH_RE = re.compile(
    r"\b("
    r"recruit(?:ing|er|ment)?|talent acquisition|human resources|"
    r"sales|account executive|account manager|business development|"
    r"marketing|brand|social media|content creator|copywriter|"
    r"legal|counsel|paralegal|accounting|payroll|tax|"
    r"supply chain|logistics|warehouse|procurement|"
    r"nursing|clinical|pharmacy|teacher|professor|adjunct|instructor"
    r")\b",
    re.IGNORECASE,
)

SWE_VETO_RE = re.compile(
    r"\b(software\s+engineer(?:ing)?|software\s+develop(?:er|ment)|sde\b|"
    r"back\s?end|front[\s-]?end|full[\s-]?stack|machine\s+learning|"
    r"data\s+scientist|data\s+engineer|mobile\s+engineer|ios\b|android)\b",
    re.IGNORECASE,
)
SEC_DOMAIN_RE = re.compile(
    r"\b(security|cyber\w*|appsec|infosec|pen\s?test\w*|vulnerability)\b",
    re.IGNORECASE,
)


def is_security(title: str) -> bool:
    if not title or NON_TECH_RE.search(title):
        return False
    if not SECURITY_RE.search(title):
        return False
    if PHYSICAL_RE.search(title) and not CYBER_ANCHOR_RE.search(
        PHYSICAL_RE.sub(" ", title)
    ):
        return False
    # "Software Engineer, Security" is a security job; "Software Engineer
    # (Security Clearance)" was already removed by PHYSICAL_RE above.
    if SWE_VETO_RE.search(title) and not SEC_DOMAIN_RE.search(title):
        return False
    return True


def is_senior(title: str) -> bool:
    return bool(SENIOR_RE.search(title or ""))


# ---------------------------------------------------------------------------
# 2. Part-time evidence
# ---------------------------------------------------------------------------
INTERN_RE = re.compile(
    r"\b(intern|interns|internship|co[\s-]?op|apprentice(?:ship)?|"
    r"student\s+(?:assistant|worker|employee|associate|analyst|technician|trainee)|"
    r"work[\s-]study|fellow(?:ship)?|ops\s+student)\b",
    re.IGNORECASE,
)

PT_TITLE_RE = re.compile(
    r"(\bpart[\s-]?time\b|\(pt\)|\bper\s+diem\b|\bprn\b|"
    r"\b(?:fall|spring|winter)\b|\byear[\s-]?round\b|"
    r"\bacademic[\s-]year\b|\bschool[\s-]year\b|\bsemester\b|"
    r"\b(?:10|12|15|16|20)\s*(?:hrs?|hours)\b)",
    re.IGNORECASE,
)
SUMMER_RE = re.compile(r"\bsummer\b", re.IGNORECASE)

PT_TEXT_RE = re.compile(
    r"(\bpart[\s-]?time\b|"
    r"\bup to (?:1\d|20) hours\b|"
    r"\bno more than (?:1\d|20) hours\b|"
    r"\b(?:during|throughout) the (?:academic|school) year\b|"
    r"\b(?:fall|spring) (?:and|&|/) (?:spring|fall|summer) semesters?\b|"
    r"\bduring the (?:fall|spring) semester\b|"
    r"\bwhile (?:enrolled|attending|in) (?:in )?(?:school|classes|college)\b|"
    r"\b(?:schedule|hours) (?:around|that work with) (?:your |their )?(?:classes|class schedule|school)\b|"
    r"\byear[\s-]?round internship\b|\bacademic[\s-]year intern\w*\b|"
    r"\bflexible (?:part[\s-]?time )?hours\b|\bper diem\b|\bas[\s-]needed basis\b)",
    re.IGNORECASE,
)
FT_TEXT_RE = re.compile(
    r"\b(full[\s-]?time|40 hours (?:per|a|/) ?week|40\s*hrs?\s*/\s*w(?:ee)?k)\b",
    re.IGNORECASE,
)
CONTRACT_RE = re.compile(
    r"\b(contract(?:or)?|freelance\w*|1099|consultant|gig|hourly|"
    r"on[\s-]demand|independent contractor)\b",
    re.IGNORECASE,
)
FREELANCE_TEXT_RE = re.compile(
    r"\b(freelance\w*|1099|independent contractor|per project|project[\s-]based|"
    r"flexible hours|set your own (?:hours|schedule)|as[\s-]needed)\b",
    re.IGNORECASE,
)

# "15-20 hours per week", "up to 20 hours a week", "20 hrs/wk", "29 hours weekly"
_HOURS_RANGE_RE = re.compile(
    r"\b(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*(?:hours|hrs?)\s*"
    r"(?:per|a|each|/|every)?\s*(?:week|wk)\b",
    re.IGNORECASE,
)
_HOURS_ONE_RE = re.compile(
    r"\b(up to|maximum of|max(?:imum)?|no more than|approximately|about|around|"
    r"averag\w+|minimum of|at least)?\s*(\d{1,2})\s*(?:\+\s*)?(?:hours|hrs?)\s*"
    r"(?:per|a|each|/|every)?\s*(?:week|wk|weekly)\b",
    re.IGNORECASE,
)


def parse_hours(text: str | None) -> tuple[int | None, int | None]:
    """(min, max) weekly hours stated in the text, or (None, None)."""
    if not text:
        return (None, None)
    m = _HOURS_RANGE_RE.search(text)
    if m:
        lo, hi = sorted((int(m.group(1)), int(m.group(2))))
        if 1 <= lo <= 60 and hi <= 60:
            return (lo, hi)
    m = _HOURS_ONE_RE.search(text)
    if m:
        qual = (m.group(1) or "").lower()
        n = int(m.group(2))
        if not 1 <= n <= 60:
            return (None, None)
        if qual in {"up to", "maximum of", "max", "maximum", "no more than"}:
            return (None, n)
        if qual in {"minimum of", "at least"}:
            return (n, None)
        return (n, n)
    return (None, None)


def format_hours(lo: int | None, hi: int | None) -> str | None:
    if lo is None and hi is None:
        return None
    if lo is None:
        return f"≤{hi}/wk"
    if hi is None:
        return f"{lo}+/wk"
    return f"{lo}/wk" if lo == hi else f"{lo}–{hi}/wk"


# ---------------------------------------------------------------------------
# 3. Experience, clearance and pay extracted from the posting text
# ---------------------------------------------------------------------------
_YEARS_RE = re.compile(
    r"\b(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)\b[^.\n]{0,40}?"
    r"\b(?:experience|exp\b|working in|in (?:cyber|information|it|security))",
    re.IGNORECASE,
)
CLEARANCE_RE = re.compile(
    r"\b(active|current|existing|must (?:have|hold|possess))\b[^.\n]{0,40}?"
    r"\b(secret|top secret|ts/sci|ts\b|public trust)\b[^.\n]{0,20}?\bclearance\b|"
    r"\b(?:ts/sci|top secret|secret)\s+clearance\s+(?:is\s+)?required\b|"
    r"\bclearance\s+(?:is\s+)?required\b",
    re.IGNORECASE,
)
CITIZEN_RE = re.compile(r"\bu\.?s\.? citizen(?:ship)?\b[^.\n]{0,20}\brequired\b|"
                        r"\bmust be a u\.?s\.? citizen\b", re.IGNORECASE)
_PAY_RE = re.compile(
    r"\$\s?(\d{2,3}(?:\.\d{2})?)\s*(?:-|–|—|to)\s*\$?\s?(\d{2,3}(?:\.\d{2})?)\s*"
    r"(?:/\s?h(?:ou)?r|per hour|an hour|hourly|/hour)",
    re.IGNORECASE,
)
_PAY_ONE_RE = re.compile(
    r"\$\s?(\d{2,3}(?:\.\d{2})?)\s*(?:/\s?h(?:ou)?r|per hour|an hour|/hour)\b",
    re.IGNORECASE,
)


def min_years(text: str | None) -> int | None:
    if not text:
        return None
    years = [int(m.group(1)) for m in _YEARS_RE.finditer(text) if int(m.group(1)) < 30]
    return min(years) if years else None


def hourly_pay(text: str | None) -> str | None:
    if not text:
        return None
    m = _PAY_RE.search(text)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return f"${lo:g}–${hi:g}/hr"
    m = _PAY_ONE_RE.search(text)
    if m:
        return f"${float(m.group(1)):g}/hr"
    return None


# ---------------------------------------------------------------------------
# 4. Location: remote (US-eligible) or Orlando area
# ---------------------------------------------------------------------------
DEFAULT_LOCAL = (
    "orlando", "kissimmee", "poinciana", "st. cloud", "saint cloud", "celebration",
    "lake nona", "lake mary", "maitland", "winter park", "altamonte springs",
    "sanford", "oviedo", "winter springs", "longwood", "heathrow", "apopka",
    "winter garden", "ocoee", "clermont", "lake buena vista", "davenport",
    "haines city", "central florida", "research park",
)
REMOTE_RE = re.compile(
    r"\b(remote|anywhere|virtual|work from home|wfh|telework|distributed|"
    r"home[\s-]based)\b",
    re.IGNORECASE,
)
US_RE = re.compile(
    r"\b(us|usa|u\.s\.a?\.?|united states|america|americas|north america|"
    r"worldwide|global|anywhere)\b|"
    r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|"
    r"MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|"
    r"WA|WV|WI|WY|DC)\b"
)
FOREIGN_RE = re.compile(
    r"\b(canada|uk|united kingdom|england|ireland|germany|france|spain|portugal|"
    r"netherlands|poland|romania|india|philippines|pakistan|brazil|mexico|"
    r"argentina|colombia|australia|new zealand|singapore|japan|israel|"
    r"emea|apac|latam|europe|eu\b|international|south africa|nigeria|kenya|"
    r"ontario|toronto|london|berlin|bangalore|bengaluru)\b",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"[;|/]|\bor\b", re.IGNORECASE)


def is_local(location: str, local: tuple[str, ...] = DEFAULT_LOCAL) -> bool:
    loc = (location or "").lower()
    if not loc:
        return False
    if "orlando" in loc or any(city in loc for city in local):
        # "Orlando" also exists outside Florida in very few postings; a
        # stated non-FL state with no FL marker loses.
        return not re.search(r",\s*(?!fl\b|florida\b)[a-z]{2}\b", loc) or bool(
            re.search(r"\b(fl|florida)\b", loc)
        )
    return False


def is_remote_us(job: Job) -> bool:
    loc = job.location or ""
    remote = bool(job.remote) or bool(REMOTE_RE.search(loc))
    if not remote:
        return False
    if job.remote and not loc.strip("—- "):
        return True
    # A multi-location string ("Remote - US; London") is fine if any piece is
    # US or unqualified-remote; "Remote, India" alone is not.
    for part in _SPLIT_RE.split(loc) or [loc]:
        part = part.strip()
        if not part:
            continue
        if FOREIGN_RE.search(part) and not US_RE.search(part):
            continue
        return True
    return False


def where(job: Job, local: tuple[str, ...] = DEFAULT_LOCAL) -> str | None:
    remote = is_remote_us(job)
    here = is_local(job.location, local)
    if remote and here:
        return "Remote / Orlando"
    if remote:
        return "Remote"
    if here:
        return "Orlando area" + (" (Hybrid)" if job.hybrid or "hybrid" in
                                 (job.location or "").lower() else "")
    return None


# ---------------------------------------------------------------------------
# 5. Category (what kind of security work) — same buckets as secnet
# ---------------------------------------------------------------------------
_SUBCATS = [
    ("SOC / Detection", re.compile(
        r"\b(soc\b|security operations|detection|threat|incident response|"
        r"forensics|dfir|malware|hunting|monitoring)\b", re.IGNORECASE)),
    ("Offensive / AppSec", re.compile(
        r"\b(appsec|application security|product security|secure code|"
        r"penetration|pen\s?test|red team|offensive|bug bounty|vulnerability)\b",
        re.IGNORECASE)),
    ("GRC / Risk", re.compile(
        r"\b(grc|governance|risk|compliance|it audit|audit|nist|fedramp|cmmc|"
        r"policy|assurance)\b", re.IGNORECASE)),
    ("Cloud / Network / IAM", re.compile(
        r"\b(cloud|network|infrastructure|identity|iam|zero trust|access)\b",
        re.IGNORECASE)),
]


def categorize(title: str) -> str:
    for name, pattern in _SUBCATS:
        if pattern.search(title or ""):
            return name
    return "Security (general)"


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------
def classify(job: Job, local: tuple[str, ...] = DEFAULT_LOCAL) -> str | None:
    """Fill kind/category/hours/flags on `job`; return the reason it was
    rejected, or None when it belongs on the list."""
    title = job.title or ""
    text = job.description or ""
    blob = f"{title}\n{text}"

    if not is_security(title):
        # Federal student titles are generic ("Student Trainee (Information
        # Technology)"); the duties say whether it is cyber work.
        federal_student = job.source == "usajobs" and INTERN_RE.search(title)
        if not (federal_student and FED_CYBER_RE.search(text)):
            return "not-security"
    if is_senior(title):
        return "senior"

    place = where(job, local)
    if place is None:
        return "location"

    etype = job.employment_type
    is_intern = etype == models.INTERN or bool(INTERN_RE.search(title))
    pt_title = bool(PT_TITLE_RE.search(title))
    pt_text = bool(PT_TEXT_RE.search(text))
    ft_text = bool(FT_TEXT_RE.search(text)) and not pt_text
    lo, hi = parse_hours(blob)

    # Weekly hours that start above 20 are not what this list is for.
    if lo is not None and lo > 20:
        return "hours"
    if lo is None and hi is not None and hi > 29:
        return "hours"

    hours_ok = (hi is not None and hi <= 20) or (hi is None and lo is not None and lo < 20)
    if etype == models.FULL_TIME:
        # The source says full-time; only the title or stated hours overrule it.
        part_time = pt_title or hours_ok
    else:
        part_time = etype == models.PART_TIME or pt_title or pt_text or hours_ok

    if job.source == "usajobs":
        # Pathways student roles count even without a part-time schedule:
        # agencies set school-year hours with the student after hire.
        if SUMMER_RE.search(title) and not pt_title:
            return "summer-only"
        if not (part_time or is_intern):
            return "not-part-time"
        kind = models.KIND_FEDERAL
    elif is_intern:
        # A summer-only title belongs to secnet-internships. A stated
        # full-time intern with no part-time cue is the same thing.
        if SUMMER_RE.search(title) and not pt_title:
            return "summer-only"
        if not part_time or (ft_text and not (pt_title or hours_ok)):
            return "not-part-time"
        kind = models.KIND_INTERNSHIP
    elif etype in (models.CONTRACT, models.TEMPORARY) or CONTRACT_RE.search(title):
        # Most "contract" security roles are 40-hour W-2 contracts. Keep only
        # the ones with part-time or freelance evidence.
        if not (part_time or FREELANCE_TEXT_RE.search(blob)) or (ft_text and not hours_ok):
            return "full-time-contract"
        kind = models.KIND_CONTRACT
    else:
        if not part_time or (ft_text and not (pt_title or hours_ok)):
            return "not-part-time"
        kind = models.KIND_JOB

    years = min_years(text)
    if years is not None and years >= 5 and kind != models.KIND_FEDERAL:
        return "experience"

    flags: list[str] = []
    if years is not None and years >= 2:
        flags.append(f"{years}+ yrs exp")
    if CLEARANCE_RE.search(blob):
        flags.append("Clearance")
    elif CITIZEN_RE.search(blob):
        flags.append("US citizen")
    if hi is not None and hi > 20:
        flags.append("Hours may exceed 20")

    job.kind = kind
    job.category = categorize(title)
    job.hours = format_hours(lo, hi)
    job.where = place
    job.flags = flags
    if not job.salary:
        job.salary = hourly_pay(text)
    return None
