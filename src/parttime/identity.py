"""Which listings are the same job.

Evidence, strongest first:
  1. the same source job id
  2. the same employer + requisition id (across sources and career sites)
  3. the same canonical apply URL
  4. the same normalized company + title + place, UNLESS something says they
     are different openings: both came from the same board under different
     ids, or both carry different requisition ids

Duplicates are merged for presentation, but provenance is kept: the record
has one primary source and a list of alternates.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Job

_TRACKING = re.compile(r"^(utm_|gh_src|gh_jid_src|source|src|ref|referrer|lever-source|"
                       r"lever-origin|iis|iisn|trid|ccuid|codes?)", re.IGNORECASE)
# Remote boards repost; the employer's own board is the better primary.
AGGREGATORS = {"remotive", "jobicy", "remoteok"}
_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|group|the)\b")


def company_key(name: str) -> str:
    n = _SUFFIX.sub(" ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "", n)


def title_key(title: str) -> str:
    t = re.sub(r"\([^)]*\b(remote|hybrid|us|usa)\b[^)]*\)", " ", (title or "").lower())
    return re.sub(r"[^a-z0-9]+", "", t)


def canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)))
    path = re.sub(r"/(apply|application)/?$", "", parts.path.rstrip("/"))
    return urlunsplit(("https", parts.netloc.lower().removeprefix("www."), path, query, ""))


def place_key(job: Job) -> str:
    # `where` is already normalized by the filter: "Remote", "Orlando area"...
    return (job.where or job.location or "").split("(")[0].strip().lower()


def _rank(job: Job) -> tuple:
    return (job.source in AGGREGATORS, job.description is None, job.posted_at or "9999")


class _UF:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, i: int) -> int:
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a: int, b: int) -> None:
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)


def _conflict(a: Job, b: Job) -> bool:
    if a.board and a.board == b.board and a.id != b.id:
        return True  # one board listing two ids = two openings
    if a.requisition_id and b.requisition_id and a.requisition_id != b.requisition_id:
        return True
    return False


def group(jobs: list[Job]) -> list[tuple[Job, list[Job]]]:
    """[(primary, alternates)]. Every input job appears exactly once."""
    uf = _UF(len(jobs))
    by: dict[tuple, int] = {}

    def link(key: tuple, i: int, check: bool = False) -> None:
        j = by.setdefault(key, i)
        if j != i and not (check and _conflict(jobs[i], jobs[j])):
            uf.union(i, j)

    for i, job in enumerate(jobs):
        link(("id", job.id), i)
        ck = company_key(job.company)
        if job.requisition_id:
            link(("req", ck, job.requisition_id.lower()), i)
        cu = canonical_url(job.url)
        if cu:
            link(("url", cu), i)
        link(("soft", ck, title_key(job.title), place_key(job)), i, check=True)

    clusters: dict[int, list[Job]] = {}
    for i, job in enumerate(jobs):
        clusters.setdefault(uf.find(i), []).append(job)
    out = []
    for members in clusters.values():
        # Drop exact-id repeats (the same role served by two searches).
        uniq = list({j.id: j for j in members}.values())
        uniq.sort(key=_rank)
        out.append((uniq[0], uniq[1:]))
    return out
