"""Description cache.

Most roles look the same run after run, so re-downloading their descriptions
every 6 hours is wasted requests. The cache stores the normalized text of
every role we fetched a description for, kept OR rejected, keyed by job id.

A cached entry is reused when:
  - the source gives a change marker (Greenhouse `updated_at`, SmartRecruiters
    `releasedDate`) and it matches, or
  - the source gives none (Workday) and the entry is younger than `ttl_days`.

Rules can then change and be re-applied to cached text without a download.

The file lives in data/cache/ (git-ignored) and is carried between GitHub
Actions runs with actions/cache, so it never bloats the repo.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import Job

MAX_TEXT = 8000      # characters kept per role: enough for every rule we run
PRUNE_DAYS = 30      # entries nobody read for this long are dropped


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.strptime(ts or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


class DescCache:
    def __init__(self, path: Path | None, ttl_days: int = 7) -> None:
        self.path = path
        self.ttl = timedelta(days=ttl_days)
        self.data: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        if path and path.exists():
            try:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.data = loaded
            except (OSError, ValueError):
                self.data = {}  # a corrupt cache is just an empty cache

    def get(self, job: Job) -> str | None:
        rec = self.data.get(job.id)
        if not rec:
            self.misses += 1
            return None
        fresh = False
        if job.updated_sig:
            fresh = rec.get("sig") == job.updated_sig
        else:
            fetched = _parse(rec.get("at"))
            fresh = bool(fetched and _now() - fetched < self.ttl)
        if not fresh:
            self.misses += 1
            return None
        rec["used"] = _iso(_now())
        self.hits += 1
        return rec.get("text")

    def put(self, job: Job, text: str | None) -> None:
        if text is None:
            return
        now = _iso(_now())
        self.data[job.id] = {"sig": job.updated_sig, "at": now, "used": now,
                             "text": text[:MAX_TEXT]}

    def text(self, job_id: str) -> str | None:
        """Raw lookup with no freshness check (labeling and evaluation tools)."""
        rec = self.data.get(job_id)
        return rec.get("text") if rec else None

    def save(self) -> None:
        if not self.path:
            return
        cutoff = _now() - timedelta(days=PRUNE_DAYS)
        keep = {k: v for k, v in self.data.items()
                if (_parse(v.get("used")) or _now()) >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(keep, f)
        tmp.replace(self.path)
