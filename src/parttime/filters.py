"""What belongs on the list, what is worth a second look, and what is out.

`classify(job, cfg)` returns a `Decision`:

  LISTED   main list. Security work, entry level, can be done from Florida
           (remote) or in the Orlando area, and the schedule is a confirmed or
           possible fit (see below).
  REVIEW   plausible but unproven. The schedule wording is seasonal or vague
           ("Fall internship", "flexible", "contract") with no hours evidence,
           or a broad title at a priority employer has no duties to judge.
  REJECTED with reason codes and the sentence that decided it.

Schedule fit levels:
  confirmed  stated weekly hours are entirely at or under 20
  possible   explicitly part-time with no hours, or a range that crosses 20
  unknown    semester/seasonal/flexible wording only  -> REVIEW

Everything here is pure functions over strings, so it can be tested offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import models
from .models import Decision, Job

# ===========================================================================
# 1. Titles
# ===========================================================================
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

# Titles that are sometimes security work and sometimes not. They only get a
# description fetch when the title also carries a student/part-time signal
# (or the employer is a priority), and then the duties decide.
AMBIGUOUS_RE = re.compile(
    r"\b("
    r"technology risk|it risk|operational risk|cyber risk|"
    r"identity (?:governance|management|services|engineer\w*|analyst)|"
    r"access management|privacy|data protection|"
    r"it compliance|technology compliance|it governance|"
    r"information technology|it\b|technology|"
    r"network\w*|systems?|infrastructure|cloud|"
    r"it operations|technical operations"
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

# Whole words only: "Identity Management" is not "manager", "Leadership" is
# not "lead", "Intelligence" is not "III".
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

# Help desk and general support stay out even with a student signal: the
# point of the wide gate is security work hiding under a broad title, not an
# all-IT list.
SUPPORT_TITLE_RE = re.compile(
    r"\b(help\s?desk|service desk|desktop support|technical support|it support|"
    r"end user|field technician|repair|av\b|audio.?visual)\b",
    re.IGNORECASE,
)

INTERN_RE = re.compile(
    r"\b(intern|interns|internship|co[\s-]?op|apprentice(?:ship)?|"
    r"student\s+(?:assistant|worker|employee|associate|analyst|technician|trainee)|"
    r"work[\s-]study|fellow(?:ship)?|ops\s+student|student)\b",
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


@dataclass
class Config:
    local: tuple[str, ...] = ()
    priority: tuple[str, ...] = ()
    max_hours: int = 20

    @classmethod
    def from_dict(cls, data: dict | None) -> Config:
        data = data or {}
        return cls(
            local=tuple(x.lower() for x in data.get("local_places", DEFAULT_LOCAL)),
            priority=tuple(x.lower() for x in data.get("priority_employers", ())),
            max_hours=int(data.get("max_hours_per_week", 20)),
        )


def is_priority(company: str, cfg: Config) -> bool:
    # Whole words: "saic" must not match "Mosaic".
    name = (company or "").lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", name)
               for p in cfg.priority)


def title_gate(job: Job, cfg: Config) -> str | None:
    """"strict" (security title), "ambiguous" (broad title, judge by duties),
    or None (not worth a description fetch)."""
    title = job.title or ""
    if is_senior(title) or NON_TECH_RE.search(title):
        return None
    if is_security(title):
        return "strict"
    if SUPPORT_TITLE_RE.search(title) or PHYSICAL_RE.search(title):
        return None
    if SWE_VETO_RE.search(title):
        return None
    if not AMBIGUOUS_RE.search(title):
        return None
    student = bool(INTERN_RE.search(title) or PT_TITLE_RE.search(title)
                   or job.employment_type in (models.INTERN, models.PART_TIME)
                   or job.source == "usajobs")
    if student or is_priority(job.company, cfg):
        return "ambiguous"
    return None


# Duties that mark a broad title as security work. Counted as distinct terms,
# so one mention of "security" in a help desk posting does not qualify.
DUTY_TERMS = {
    "siem": r"\bsiem\b|splunk|sentinel|qradar|elastic security",
    "vulnerability": r"vulnerabilit\w+|patch management|nessus|qualys|tenable",
    "incident": r"incident (?:response|handling|management)|security incidents?",
    "access review": r"access reviews?|user access|least privilege|provisioning|"
                     r"role[\s-]based access|identity governance|\biam\b|\bmfa\b|"
                     r"multi[\s-]factor|single sign[\s-]on|\bsso\b|okta|sailpoint|cyberark",
    "risk assessment": r"risk assessments?|risk register|third[\s-]party risk|vendor risk",
    "frameworks": r"\bnist\b|iso\s?27001|soc\s?2|\bcis\b controls|pci[\s-]dss|hipaa|"
                  r"fedramp|cmmc|\bsox\b|\bitgc\b|\brmf\b|\bato\b",
    "controls": r"security controls|control testing|it general controls|audit evidence",
    "threat": r"threat (?:intel\w*|hunting|detection|model\w*)|indicators of compromise|"
              r"\biocs?\b|mitre att&ck|\battack\b framework",
    "network defense": r"firewalls?|\bids\b|\bips\b|intrusion detection|edr\b|"
                       r"endpoint (?:detection|protection)|crowdstrike|defender",
    "monitoring": r"security (?:monitoring|alerts|events|logs)|log analysis|soc\b",
    "phishing": r"phishing|security awareness|social engineering",
    "pentest": r"penetration test\w*|pen\s?test\w*|red team|offensive security",
    "encryption": r"encryption|\bpki\b|certificates? management|key management",
    "forensics": r"forensics|malware analysis|reverse engineering",
    "policy": r"security polic(?:y|ies)|information security program|data classification",
}
_DUTY_RES = {k: re.compile(v, re.IGNORECASE) for k, v in DUTY_TERMS.items()}
DUTY_THRESHOLD = 3


def security_duties(text: str | None) -> list[str]:
    if not text:
        return []
    return [k for k, rx in _DUTY_RES.items() if rx.search(text)]


# ===========================================================================
# 2. Sentences and evidence
# ===========================================================================
_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+|\s+[•·]\s+")


def sentence_at(text: str, pos: int, limit: int = 220) -> str:
    """The sentence containing character `pos`, trimmed for storage."""
    start = 0
    for m in _SENT_SPLIT.finditer(text):
        if m.end() <= pos:
            start = m.end()
        else:
            end = m.start()
            break
    else:
        end = len(text)
    s = text[start:end].strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


# ===========================================================================
# 3. Hours
# ===========================================================================
_HOURS_RANGE_RE = re.compile(
    r"\b(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*(?:hours|hrs?)\s*"
    r"(?:per|a|each|/|every)?\s*(?:week|wk)\b",
    re.IGNORECASE,
)
_HOURS_ONE_RE = re.compile(
    r"\b(up to|maximum of|max(?:imum)?|no more than|not (?:to )?exceed|"
    r"approximately|about|around|averag\w+|minimum of|at least|min(?:imum)?)?"
    r"\s*(\d{1,2})\s*(?:\+\s*)?(?:hours|hrs?)\s*(?:per|a|each|/|every)?\s*"
    r"(?:week|wk|weekly)\b",
    re.IGNORECASE,
)
# "20 hours during semesters" has no "per week" but means it.
_HOURS_TERM_RE = re.compile(
    r"\b(up to |no more than )?(\d{1,2})\s*(?:hours|hrs?)\s+(?:during|in|throughout|each|per) "
    r"(?:the )?(?:semesters?|school year|academic year|fall|spring|school weeks?)\b",
    re.IGNORECASE,
)
_UPPER = {"up to", "maximum of", "max", "maximum", "no more than", "not exceed",
          "not to exceed"}
_LOWER = {"minimum of", "at least", "min", "minimum"}
_SUMMER_CTX = re.compile(r"\bsummer\b", re.IGNORECASE)
_SEMESTER_CTX = re.compile(
    r"\b(semesters?|school year|academic year|fall|spring|during (?:the )?(?:school|classes)|"
    r"while (?:in|attending) (?:school|classes)|class schedule)\b",
    re.IGNORECASE,
)


@dataclass
class HoursMention:
    lo: int | None
    hi: int | None
    season: str          # "summer" | "semester" | "general"
    sentence: str
    pos: int


def hour_mentions(text: str | None) -> list[HoursMention]:
    if not text:
        return []
    out: list[HoursMention] = []
    taken: list[tuple[int, int]] = []
    for m in _HOURS_RANGE_RE.finditer(text):
        lo, hi = sorted((int(m.group(1)), int(m.group(2))))
        if 1 <= lo and hi <= 60:
            out.append(_mention(text, m, lo, hi))
            taken.append(m.span())
    for m in _HOURS_ONE_RE.finditer(text):
        if any(a <= m.start(2) < b for a, b in taken):
            continue
        n = int(m.group(2))
        if not 1 <= n <= 60:
            continue
        qual = re.sub(r"\s+", " ", (m.group(1) or "").lower())
        if qual in _UPPER:
            lo, hi = None, n
        elif qual in _LOWER:
            lo, hi = n, None
        else:
            lo, hi = n, n
        out.append(_mention(text, m, lo, hi))
        taken.append(m.span())
    for m in _HOURS_TERM_RE.finditer(text):
        if any(a <= m.start(2) < b for a, b in taken) or any(h.pos == m.start() for h in out):
            continue
        n = int(m.group(2))
        if 1 <= n <= 60:
            lo = None if m.group(1) else n
            out.append(HoursMention(lo, n, "semester", sentence_at(text, m.start()), m.start()))
    out.sort(key=lambda h: h.pos)
    return out


def _mention(text: str, m: re.Match, lo, hi) -> HoursMention:
    sentence = sentence_at(text, m.start())
    # Season comes from the same sentence: "20 hours during the semester and
    # up to 40 in the summer" has two mentions with two seasons.
    window = text[max(0, m.start() - 60): m.end() + 40]
    if _SUMMER_CTX.search(window) and not _SEMESTER_CTX.search(
            text[max(0, m.start() - 25): m.end() + 25]):
        season = "summer"
    elif _SEMESTER_CTX.search(window):
        season = "semester"
    else:
        season = "general"
    return HoursMention(lo, hi, season, sentence, m.start())


def parse_hours(text: str | None) -> tuple[int | None, int | None]:
    """(min, max) of the first non-summer weekly-hours mention."""
    for h in hour_mentions(text):
        if h.season != "summer":
            return (h.lo, h.hi)
    return (None, None)


def format_hours(lo: int | None, hi: int | None) -> str | None:
    if lo is None and hi is None:
        return None
    if lo is None:
        return f"≤{hi}/wk"
    if hi is None:
        return f"{lo}+/wk"
    return f"{lo}/wk" if lo == hi else f"{lo}–{hi}/wk"


# ===========================================================================
# 4. Part-time evidence
# ===========================================================================
PT_EXPLICIT_RE = re.compile(r"\bpart[\s-]?time\b|\(pt\)|\bper\s+diem\b|\bprn\b",
                            re.IGNORECASE)
PT_TITLE_RE = re.compile(
    r"(\bpart[\s-]?time\b|\(pt\)|\bper\s+diem\b|\bprn\b|"
    r"\b(?:fall|spring|winter)\b|\byear[\s-]?round\b|"
    r"\bacademic[\s-]year\b|\bschool[\s-]year\b|\bsemester\b|"
    r"\b(?:10|12|15|16|20)\s*(?:hrs?|hours)\b)",
    re.IGNORECASE,
)
# Seasonal or flexible wording: says "during school", not "20 hours or less".
SEASONAL_RE = re.compile(
    r"\b(fall|spring|winter|year[\s-]?round|academic[\s-]year|school[\s-]year|"
    r"semester|co[\s-]?op)\b",
    re.IGNORECASE,
)
SEASONAL_TEXT_RE = re.compile(
    r"(\b(?:during|throughout) the (?:academic|school) year\b|"
    r"\b(?:fall|spring) (?:and|&|/) (?:spring|fall|summer) semesters?\b|"
    r"\bduring the (?:fall|spring) semester\b|"
    r"\bwhile (?:enrolled|attending|in) (?:in )?(?:school|classes|college)\b|"
    r"\b(?:schedule|hours) (?:around|that work with) (?:your |their )?"
    r"(?:classes|class schedule|school)\b|"
    r"\byear[\s-]?round internship\b|\bacademic[\s-]year intern\w*\b|"
    r"\bflexible (?:hours|schedule)\b|\bas[\s-]needed\b|\bset your own (?:hours|schedule)\b)",
    re.IGNORECASE,
)
# A full-time JOB, not a full-time STUDENT. Internship postings constantly say
# "must be enrolled as a full-time student" or "may convert to full-time".
FT_JOB_RE = re.compile(
    r"(\b(?:is|be|this is) an? full[\s-]?time\b|"
    r"\bfull[\s-]?time (?:position|role|job|schedule|internship|co[\s-]?op|"
    r"employment opportunity|commitment|hours)\b|"
    r"\b(?:requires?|expected to work|work) (?:a )?(?:minimum of )?(?:3[2-9]|40) hours\b|"
    r"\b40\s*(?:hours|hrs?)\s*(?:per|a|/)\s*(?:week|wk)\b)",
    re.IGNORECASE,
)
FT_NOT_JOB_RE = re.compile(
    r"full[\s-]?time (?:students?|enrol\w*|undergrad\w*|graduate|degree|"
    r"employees?|offers?|employment after|role after|position after|opportunit\w+ after)|"
    r"(?:convert|conversion|transition) (?:to|into) (?:a )?full[\s-]?time|"
    r"enrolled full[\s-]?time",
    re.IGNORECASE,
)
SUMMER_FT_RE = re.compile(
    r"(full[\s-]?time (?:during|in|over|for) (?:the )?summer|"
    r"summer[^.]{0,40}\bfull[\s-]?time|(?:40|3[2-9]) hours[^.]{0,30}summer|"
    r"summer[^.]{0,30}(?:40|3[2-9]) hours|"
    r"\b(?:40|3[2-9])(?:\s*(?:hours|hrs?))?(?:\s*(?:per|a|/)\s*week)?\s+(?:during|in|over|for) "
    r"(?:the )?summer)",
    re.IGNORECASE,
)
SUMMER_RE = re.compile(r"\bsummer\b", re.IGNORECASE)
CONTINUES_RE = re.compile(
    r"\b(continue|extend\w*|transition)\w*\b[^.]{0,40}\b(part[\s-]?time|"
    r"fall|school year|academic year|semester)\b",
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


def full_time_job(text: str) -> str | None:
    """The sentence saying this is a full-time job, ignoring summer-only and
    full-time-student wording."""
    for m in FT_JOB_RE.finditer(text or ""):
        sentence = sentence_at(text, m.start())
        if FT_NOT_JOB_RE.search(sentence) or SUMMER_RE.search(sentence):
            continue
        return sentence
    return None


# ===========================================================================
# 5. Experience, clearance, pay
# ===========================================================================
_YEARS_RE = re.compile(
    r"\b(?:minimum (?:of )?|at least )?(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?"
    r"(?:years?|yrs?)(?:\s+of)?(?:\s+[\w/&-]+){0,4}?\s+(?:experience|exp\b)",
    re.IGNORECASE,
)
PREFERRED_RE = re.compile(
    r"\b(preferred|preferably|desired|nice to have|a plus|bonus|ideally|"
    r"is an advantage|helpful)\b",
    re.IGNORECASE,
)
ALTERNATIVE_RE = re.compile(
    r"\b(or equivalent|equivalent (?:combination|experience)|in lieu of|"
    r"or (?:a |an )?(?:bachelor|master|associate|degree)|or relevant (?:coursework|education)|"
    r"or (?:a )?combination)\b",
    re.IGNORECASE,
)
# The years headline only counts when it describes the applicant, not the
# company ("over the past 5 years our team has...").
_NOT_APPLICANT_RE = re.compile(r"\b(our|we have|we've|the company|founded)\b", re.IGNORECASE)


@dataclass
class Experience:
    years: int
    level: str        # "required" | "preferred" | "alternative"
    sentence: str


def experience(text: str | None) -> list[Experience]:
    if not text:
        return []
    out = []
    for m in _YEARS_RE.finditer(text):
        n = int(m.group(1))
        if n >= 30:
            continue
        sentence = sentence_at(text, m.start())
        if _NOT_APPLICANT_RE.search(sentence) and not re.search(
                r"\b(you|candidate|applicant|require|must|minimum)\b", sentence, re.I):
            continue
        if PREFERRED_RE.search(sentence):
            level = "preferred"
        elif ALTERNATIVE_RE.search(sentence):
            level = "alternative"
        else:
            level = "required"
        out.append(Experience(n, level, sentence))
    return out


def min_years(text: str | None) -> int | None:
    """Smallest REQUIRED years in the text (kept for callers that want one number)."""
    req = [e.years for e in experience(text) if e.level == "required"]
    return min(req) if req else None


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


# ===========================================================================
# 6. Location: can this be done from Kissimmee?
# ===========================================================================
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
    r"worldwide|global|anywhere)\b",
    re.IGNORECASE,
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

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY", "district of columbia": "DC",
}
_STATE_NAME_RE = re.compile(r"\b(" + "|".join(sorted(STATES, key=len, reverse=True)) + r")\b",
                            re.IGNORECASE)
_STATE_ABBR_RE = re.compile(r"\b(" + "|".join(sorted(set(STATES.values()))) + r")\b")

_INCLUDE_RE = re.compile(
    r"(?:(?:must|required to|need to|will need to)\s+(?:reside|live|be located|be based)\s+in|"
    r"(?:we|currently)\s+(?:can|are able to|only)\s+(?:only\s+)?(?:hire|employ)\s+"
    r"(?:candidates\s+)?(?:in|from|residing in)(?:\s+the following states)?|"
    r"open to (?:candidates|applicants|residents) (?:in|of|located in)|"
    r"(?:eligible|approved|hiring) (?:states|locations)(?:\s+include)?|"
    r"remote (?:in|within) the following states)\s*:?\s*(.{0,300}?)(?:\.\s|$|\n)",
    re.IGNORECASE,
)
_EXCLUDE_RE = re.compile(
    r"(?:(?:not|unable to|cannot|can't|do not|don't)\s+(?:able to\s+)?"
    r"(?:hire|employ|consider (?:candidates|applicants))\s+(?:who reside |residing |located )?"
    r"(?:in|from)|excluding(?: the states of)?|with the exception of|except(?: for)?(?: residents of)?)"
    r"\s*:?\s*(.{0,300}?)(?:\.\s|$|\n)",
    re.IGNORECASE,
)


def _states_in(text: str) -> set[str]:
    found = {STATES[m.group(1).lower()] for m in _STATE_NAME_RE.finditer(text)}
    found |= {m.group(1) for m in _STATE_ABBR_RE.finditer(text)}
    return found


def is_local(location: str, local: tuple[str, ...] = DEFAULT_LOCAL) -> bool:
    loc = (location or "").lower()
    if not loc:
        return False
    if any(city in loc for city in local or DEFAULT_LOCAL):
        # A stated non-FL state with no FL marker loses ("Orlando, MI").
        if re.search(r"\b(fl|florida)\b", loc):
            return True
        return not re.search(r",\s*(?!fl\b|florida\b)[a-z]{2}\b", loc)
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


@dataclass
class Place:
    work_mode: str
    florida: str           # yes | likely | no | unknown
    where: str | None      # display label, None = out of range
    evidence: str = ""
    flags: list[str] = field(default_factory=list)


def place(job: Job, cfg: Config) -> Place:
    loc = job.location or ""
    text = job.description or ""
    local = is_local(loc, cfg.local)
    hybrid = bool(job.hybrid) or bool(re.search(r"\bhybrid\b", loc, re.I))
    remote = is_remote_us(job)
    mode = "remote" if remote else "hybrid" if hybrid else (
        "onsite" if loc.strip("—- ") else "unknown")

    if not remote:
        if local:
            return Place(mode, "yes", "Orlando area" + (" (Hybrid)" if hybrid else ""),
                         f"location: {loc}")
        return Place(mode, "no", None, f"location: {loc}")

    # Remote: look for applicant-location rules in the posting text first.
    # Office lists and HQ cities are NOT restrictions, so only explicit
    # "must reside / can hire in / cannot hire in" phrasing counts.
    for m in _EXCLUDE_RE.finditer(text):
        if "FL" in _states_in(m.group(1)):
            return Place(mode, "no", None, sentence_at(text, m.start()))
    for m in _INCLUDE_RE.finditer(text):
        states = _states_in(m.group(1))
        if states:
            sentence = sentence_at(text, m.start())
            if "FL" in states:
                label = "Remote / Orlando" if local else "Remote"
                return Place(mode, "yes", label, sentence)
            return Place(mode, "no", None, sentence)

    label = "Remote / Orlando" if local else "Remote"
    loc_states = _states_in(re.sub(r"\b(US|USA)\b", "", loc))
    if "FL" in loc_states:
        return Place(mode, "yes", label, f"location: {loc}")
    if US_RE.search(loc) or job.source == "usajobs":
        return Place(mode, "likely", label, f"location: {loc}")
    if loc_states:
        # "Remote - New York" may mean remote-in-NY or just the HQ.
        return Place(mode, "unknown", label, f"location: {loc}",
                     ["Check state eligibility"])
    return Place(mode, "unknown", label, f"location: {loc or '—'}", ["Location unverified"])


def where(job: Job, local: tuple[str, ...] = DEFAULT_LOCAL) -> str | None:
    """Display label only (kept for the cheap pre-check)."""
    return place(job, Config(local=local)).where


# ===========================================================================
# 7. Category
# ===========================================================================
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
        r"policy|assurance|privacy)\b", re.IGNORECASE)),
    ("Cloud / Network / IAM", re.compile(
        r"\b(cloud|network|infrastructure|identity|iam|zero trust|access)\b",
        re.IGNORECASE)),
]


def categorize(title: str, duties: list[str] | None = None) -> str:
    for name, pattern in _SUBCATS:
        if pattern.search(title or ""):
            return name
    duties = duties or []
    if {"siem", "incident", "monitoring", "threat"} & set(duties):
        return "SOC / Detection"
    if {"risk assessment", "frameworks", "controls", "policy"} & set(duties):
        return "GRC / Risk"
    if "access review" in duties:
        return "Cloud / Network / IAM"
    return "Security (general)"


# ===========================================================================
# 8. The decision
# ===========================================================================
FED_CYBER_RE = re.compile(
    r"\b(cyber\w*|information security|infosec|information assurance|"
    r"incident response|vulnerabilit\w+|security operations)\b",
    re.IGNORECASE,
)


def _reject(code: str, evidence: dict | None = None) -> Decision:
    return Decision(models.REJECTED, [code], evidence or {})


def classify(job: Job, cfg: Config | None = None) -> Decision:
    """Decide where `job` goes. On LISTED/REVIEW the job's display fields are
    filled in; on REJECTED it is left alone."""
    cfg = cfg or Config(local=DEFAULT_LOCAL)
    title = job.title or ""
    text = job.description or ""
    blob = f"{title}\n{text}"
    evidence: dict[str, str] = {}
    flags: list[str] = []
    missing: list[str] = []
    review_reasons: list[str] = []
    priority = is_priority(job.company, cfg)

    # ---- what kind of work
    if is_senior(title):
        return _reject("senior", {"title": title})
    gate = title_gate(job, cfg)
    duties = security_duties(text)
    if job.source == "usajobs" and gate != "strict":
        # Federal titles are generic ("Student Trainee (Information
        # Technology)"); the duties decide.
        if not FED_CYBER_RE.search(text):
            return _reject("not-security", {"title": title})
        gate = "strict"
    if gate is None:
        return _reject("not-security", {"title": title})
    if gate == "ambiguous":
        if not text:
            if priority:
                review_reasons.append("broad-title-no-description")
                missing.append("job description")
            else:
                return _reject("no-duty-evidence", {"title": title})
        elif len(duties) < DUTY_THRESHOLD:
            return _reject("duties-not-security",
                           {"duties": ", ".join(duties) or "none found"})
        else:
            evidence["duties"] = ", ".join(duties)
            flags.append("Security duties, broad title")

    # ---- where
    p = place(job, cfg)
    evidence["location"] = p.evidence
    if p.where is None:
        return _reject("florida-ineligible" if p.work_mode == "remote" else "location",
                       {"location": p.evidence})
    flags += p.flags
    if p.florida == "unknown":
        missing.append("applicant location rules")

    # ---- schedule
    etype = job.employment_type
    is_intern = etype == models.INTERN or bool(INTERN_RE.search(title))
    mentions = hour_mentions(blob)
    term = [h for h in mentions if h.season != "summer"]
    summer = [h for h in mentions if h.season == "summer"]
    primary = term[0] if term else None
    summer_ft = bool(SUMMER_FT_RE.search(blob)) or any(
        (h.lo or 0) > cfg.max_hours or (h.hi or 0) > cfg.max_hours for h in summer)
    ft_sentence = full_time_job(text)
    explicit_pt = (etype == models.PART_TIME or bool(PT_EXPLICIT_RE.search(title))
                   or bool(PT_EXPLICIT_RE.search(text)))
    seasonal = bool(SEASONAL_RE.search(title)) or bool(SEASONAL_TEXT_RE.search(text))
    summer_only_title = bool(SUMMER_RE.search(title)) and not PT_TITLE_RE.search(title)

    lo = hi = None
    fit = None
    if primary is not None:
        lo, hi = primary.lo, primary.hi
        evidence["hours"] = primary.sentence
        if lo is not None and lo > cfg.max_hours:
            return _reject("hours-over-limit", {"hours": primary.sentence})
        if hi is not None and hi <= cfg.max_hours:
            fit = models.FIT_CONFIRMED
        else:
            fit = models.FIT_POSSIBLE   # crosses the limit or no upper bound
            flags.append("Hours may exceed 20")
    if fit is None:
        if etype == models.FULL_TIME and not PT_EXPLICIT_RE.search(title):
            return _reject("full-time", {"employment_type": "full-time (source)"})
        if ft_sentence and not explicit_pt:
            return _reject("full-time", {"schedule": ft_sentence})
        if explicit_pt:
            fit = models.FIT_POSSIBLE
            flags.append("Hours unverified")
            missing.append("weekly hours")
            m = PT_EXPLICIT_RE.search(text)
            evidence["schedule"] = sentence_at(text, m.start()) if m else (
                "title says part-time" if PT_EXPLICIT_RE.search(title)
                else "source lists part-time")
        elif summer_only_title and not CONTINUES_RE.search(text):
            return _reject("summer-only", {"title": title})
        elif seasonal or (job.source == "usajobs" and is_intern):
            fit = models.FIT_UNKNOWN
            missing.append("weekly hours")
            m = SEASONAL_TEXT_RE.search(text)
            evidence["schedule"] = sentence_at(text, m.start()) if m else f"title: {title}"
        elif etype in (models.CONTRACT, models.TEMPORARY) or CONTRACT_RE.search(title):
            if FREELANCE_TEXT_RE.search(blob):
                fit = models.FIT_UNKNOWN
                missing.append("weekly hours")
                m = FREELANCE_TEXT_RE.search(blob)
                evidence["schedule"] = sentence_at(blob, m.start())
            else:
                return _reject("full-time-contract", {"title": title})
        else:
            return _reject("not-part-time", {"title": title})
    elif ft_sentence and fit == models.FIT_POSSIBLE and not explicit_pt:
        # "15+ hours" next to "this is a full-time position" is not a fit.
        return _reject("full-time", {"schedule": ft_sentence})
    if summer_ft:
        flags.append("Full-time in summer")
        m = SUMMER_FT_RE.search(blob)
        evidence["summer"] = sentence_at(blob, m.start()) if m else summer[0].sentence

    # ---- kind
    if job.source == "usajobs":
        kind = models.KIND_FEDERAL
        flags.append("Federal: check eligibility")
    elif is_intern:
        kind = models.KIND_INTERNSHIP
    elif etype in (models.CONTRACT, models.TEMPORARY) or CONTRACT_RE.search(title):
        kind = models.KIND_CONTRACT
    else:
        kind = models.KIND_JOB

    # ---- experience (title is entry level; now check what the text asks)
    exp = experience(text)
    required = [e for e in exp if e.level == "required"]
    if required:
        worst = min(required, key=lambda e: e.years)
        evidence["experience"] = worst.sentence
        if worst.years >= 5 and kind != models.KIND_FEDERAL:
            return _reject("experience-required", {"experience": worst.sentence})
        if worst.years >= 2:
            flags.append(f"{worst.years}+ yrs exp")
    for e in exp:
        if e.level == "preferred" and e.years >= 2:
            flags.append(f"{e.years}+ yrs preferred")
            evidence.setdefault("experience", e.sentence)
            break
    for e in exp:
        if e.level == "alternative" and e.years >= 2 and not required:
            flags.append(f"{e.years} yrs or equivalent")
            evidence.setdefault("experience", e.sentence)
            break

    m = CLEARANCE_RE.search(blob)
    if m:
        flags.append("Clearance")
        evidence["clearance"] = sentence_at(blob, m.start())
    elif CITIZEN_RE.search(blob):
        flags.append("US citizen")

    # ---- fill in and route
    job.kind = kind
    job.category = categorize(title, duties)
    job.schedule_fit = fit
    job.hours_min, job.hours_max = lo, hi
    job.hours = format_hours(lo, hi)
    job.summer_commitment = summer_ft
    job.work_mode = p.work_mode
    job.florida_eligibility = p.florida
    job.where = p.where
    job.priority = priority
    job.flags = list(dict.fromkeys(flags))
    job.evidence = {k: v for k, v in evidence.items() if v}
    if not job.salary:
        job.salary = hourly_pay(text)

    if fit == models.FIT_UNKNOWN:
        review_reasons.append("schedule-unknown")
    reasons = review_reasons or [f"schedule-{fit}"]
    verdict = models.REVIEW if review_reasons else models.LISTED
    job.queue = verdict
    return Decision(verdict, reasons, job.evidence, missing)
