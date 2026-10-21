"""Job lifecycle: open, missing, closed, stale, filtered.

The rule that matters most: only evidence can close a job.

  seen this run (and still passes)        -> open
  seen this run but now fails the rules   -> filtered (off the list, archived later)
  absent from a COMPLETE read of every
    board it lives on                     -> missing, then closed after
                                             CLOSE_SCANS complete absences
                                             spanning at least CLOSE_HOURS
  its boards errored or read partially    -> unchanged; last_checked moves only
                                             if the board answered at all
  not seen for STALE_DAYS                 -> stale (shown as unverified)

`last_seen` moves only when the role was actually seen. `last_checked` moves
when any board it lives on answered. A failed read moves neither.

Closed, filtered and long-stale records leave jobs.json after ARCHIVE_DAYS and
are appended to data/archive.jsonl as a compact history of which employers
post school-year security work.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import identity, models
from .models import Job

CLOSE_SCANS = 2
CLOSE_HOURS = 12
STALE_DAYS = 4
ARCHIVE_DAYS = 21
STALE_ARCHIVE_DAYS = 30

COMPLETE = "complete"
PARTIAL = "partial"
FAILED = "failed"


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.strptime(ts or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


class Index:
    """Finds the stored record a fresh listing belongs to."""

    def __init__(self, records: dict) -> None:
        self.by_id: dict[str, str] = {}
        self.by_req: dict[tuple, str] = {}
        self.by_url: dict[str, str] = {}
        for key, rec in records.items():
            self.add(key, rec)

    def add(self, key: str, rec: dict) -> None:
        for sid in rec.get("source_job_ids") or [key]:
            self.by_id[sid] = key
        if rec.get("requisition_id"):
            self.by_req[(identity.company_key(rec.get("company", "")),
                         str(rec["requisition_id"]).lower())] = key
        for alt in [rec, *(rec.get("alternate_sources") or [])]:
            cu = identity.canonical_url(alt.get("url", ""))
            if cu:
                self.by_url[cu] = key

    def find(self, jobs: list[Job]) -> str | None:
        for j in jobs:
            if j.id in self.by_id:
                return self.by_id[j.id]
        for j in jobs:
            if j.requisition_id:
                k = self.by_req.get((identity.company_key(j.company), j.requisition_id.lower()))
                if k:
                    return k
        for j in jobs:
            k = self.by_url.get(identity.canonical_url(j.url))
            if k:
                return k
        return None


def update(records: dict, groups: list[tuple[Job, list[Job]]], seen_rejected: dict[str, str],
           boards: dict[str, str], now: str, closing_allowed: bool = True) -> dict:
    """Apply one run. `boards` maps board label -> COMPLETE/PARTIAL/FAILED for
    every board this run tried. `seen_rejected` maps job id -> reason for roles
    seen this run that no longer pass. Returns {"archived": [...], counts...}."""
    index = Index(records)
    now_dt = _parse(now) or datetime.now(UTC)
    touched: set[str] = set()
    counts = {"new": 0, "reopened": 0, "missing": 0, "closed": 0, "stale": 0, "filtered": 0}

    for primary, alts in groups:
        members = [primary, *alts]
        key = index.find(members) or primary.id
        prior = records.get(key) or {}
        rec = primary.record()
        rec["key"] = key
        rec["first_seen"] = prior.get("first_seen") or now
        rec["last_seen"] = now
        rec["last_checked"] = now
        if not prior:
            counts["new"] += 1
        elif prior.get("status") in (models.MISSING, models.STALE, models.CLOSED, models.FILTERED):
            counts["reopened"] += 1
        rec["status"] = models.OPEN
        rec["missing_since"] = None
        rec["missing_scans"] = 0
        ids = set(prior.get("source_job_ids") or []) | {j.id for j in members}
        rec["source_job_ids"] = sorted(ids)
        boards_of = set(prior.get("boards") or []) | {j.board for j in members if j.board}
        rec["boards"] = sorted(boards_of)
        alt_map = {a["id"]: a for a in prior.get("alternate_sources") or []}
        for a in alts:
            alt_map[a.id] = {"id": a.id, "source": a.source, "url": a.url, "board": a.board}
        alt_map.pop(primary.id, None)
        rec["alternate_sources"] = sorted(alt_map.values(), key=lambda a: a["id"])
        rec["primary_source"] = primary.source
        records[key] = rec
        index.add(key, rec)
        touched.add(key)

    for key, rec in records.items():
        if key in touched or rec.get("status") in (models.CLOSED, models.FILTERED):
            continue
        ids = rec.get("source_job_ids") or [key]
        hit = next((seen_rejected[i] for i in ids if i in seen_rejected), None)
        if hit:
            rec["status"] = models.FILTERED
            rec["filtered_reason"] = hit
            rec["ended_at"] = now
            rec["last_seen"] = now
            rec["last_checked"] = now
            counts["filtered"] += 1
            continue
        states = [boards.get(b) for b in rec.get("boards") or []]
        if any(s in (COMPLETE, PARTIAL) for s in states):
            rec["last_checked"] = now
        all_complete = bool(states) and all(s == COMPLETE for s in states)
        if all_complete and closing_allowed:
            if rec.get("status") != models.MISSING:
                rec["status"] = models.MISSING
                rec["missing_since"] = now
                rec["missing_scans"] = 1
                counts["missing"] += 1
            else:
                rec["missing_scans"] = int(rec.get("missing_scans") or 0) + 1
                since = _parse(rec.get("missing_since")) or now_dt
                if (rec["missing_scans"] >= CLOSE_SCANS
                        and now_dt - since >= timedelta(hours=CLOSE_HOURS)):
                    rec["status"] = models.CLOSED
                    rec["ended_at"] = now
                    counts["closed"] += 1
            continue
        last_seen = _parse(rec.get("last_seen")) or now_dt
        if (rec.get("status") in (models.OPEN, models.MISSING)
                and now_dt - last_seen >= timedelta(days=STALE_DAYS)):
            rec["status"] = models.STALE
            counts["stale"] += 1

    archived = []
    for key in list(records):
        rec = records[key]
        status = rec.get("status")
        ended = _parse(rec.get("ended_at"))
        last_seen = _parse(rec.get("last_seen")) or now_dt
        done = (status in (models.CLOSED, models.FILTERED) and ended
                and now_dt - ended >= timedelta(days=ARCHIVE_DAYS))
        dead = status == models.STALE and now_dt - last_seen >= timedelta(days=STALE_ARCHIVE_DAYS)
        if done or dead:
            archived.append(archive_row(rec, "stale" if dead else status, now))
            del records[key]
    counts["archived_count"] = len(archived)
    return {"archived": archived, **counts}


def archive_row(rec: dict, ended_as: str, now: str) -> dict:
    return {k: rec.get(k) for k in (
        "key", "company", "title", "kind", "queue", "schedule_fit", "category", "where",
        "hours", "salary", "first_seen", "last_seen", "url", "primary_source",
    )} | {"ended_as": ended_as, "archived_at": now}


def visible(records: dict) -> list[dict]:
    """Records the dashboard shows: open, missing (may be closed) and stale."""
    rows = [r for r in records.values()
            if r.get("status") in (models.OPEN, models.MISSING, models.STALE)]
    for r in rows:
        flags = [f for f in r.get("flags") or [] if f not in ("May be closed", "Unverified")]
        if r["status"] == models.MISSING:
            flags.append("May be closed")
        elif r["status"] == models.STALE:
            flags.append("Unverified")
        r["display_flags"] = flags
    return sorted(rows, key=lambda r: (r.get("posted_at") or r.get("first_seen") or ""),
                  reverse=True)


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    # v1 stores had no source_job_ids/boards; give each record its own id.
    for key, rec in data.items():
        rec.setdefault("key", key)
        rec.setdefault("source_job_ids", [rec.get("id") or key])
        rec.setdefault("boards", [rec["board"]] if rec.get("board") else [])
        if rec.get("status") == models.CLOSED and not rec.get("ended_at"):
            rec["ended_at"] = rec.get("closed_at")
    return data


def append_archive(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
