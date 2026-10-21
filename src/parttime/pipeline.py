"""One run: fetch every source, classify, dedupe, update the lifecycle,
publish, alert."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

from . import filters, identity, models, notify, render, store
from .cache import DescCache
from .models import Fetch, Job
from .net import HostLimiter, Net
from .sources import ats, remote_boards, usajobs
from .sources.base import Ctx

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DOCS = ROOT / "docs"
README = ROOT / "README.md"
CACHE = DATA / "cache" / "descriptions.json.gz"

GLOBAL_CONCURRENCY = 32
PER_HOST = 8
BOARD_TIMEOUT = 150
DECISION_SAMPLE = 300           # rejected candidates kept in decisions.jsonl
DETAIL_SOURCES = {"greenhouse", "workday", "smartrecruiters"}
TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests swap in a MockTransport
USER_AGENT = "secnet-parttime/2.0 (+https://github.com/ihrtkaii/secnet-parttime)"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_config() -> dict:
    path = DATA / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_companies() -> list[dict]:
    """The secnet registry, deduped by board, plus any local additions."""
    rows = json.loads((DATA / "companies.json").read_text(encoding="utf-8"))
    extra = DATA / "companies_extra.json"
    if extra.exists():
        rows += json.loads(extra.read_text(encoding="utf-8"))
    seen, out = set(), []
    for c in rows:
        if not isinstance(c, dict) or c.get("ats") not in ats.FETCHERS:
            continue
        key = (c["ats"], c.get("slug"), c.get("site"))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def board_label(c: dict) -> str:
    return f"{c['ats']}:{c['slug']}" + (f":{c['site']}" if c.get("site") else "")


def scope_fingerprint(cfg: filters.Config) -> str:
    blob = json.dumps([ats.SCOPE, remote_boards.SCOPE, usajobs.SCOPE, sorted(cfg.priority)],
                      sort_keys=True, default=list)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def make_want(cfg: filters.Config):
    """Cheap pre-check deciding which roles are worth a description fetch:
    a security or plausible title, and a location that could fit (or one too
    vague to judge until the detail page fills it in)."""
    import re
    vague = re.compile(r"^(—|-|)$|\d+\s+locations?|multiple", re.IGNORECASE)

    def want(job: Job) -> bool:
        if filters.title_gate(job, cfg) is None:
            return False
        return (filters.place(job, cfg).where is not None
                or bool(vague.search(job.location.strip())))
    return want


async def _bounded(coro, label: str) -> tuple[str, Fetch]:
    try:
        async with asyncio.timeout(BOARD_TIMEOUT):
            return label, await coro
    except Exception as exc:  # noqa: BLE001 - one bad board never sinks the run
        return label, Fetch([], ok=False, error=f"{type(exc).__name__}: {exc}"[:200])


async def fetch_everything(companies: list[dict], ctx: Ctx, sources: set[str]):
    limiter = HostLimiter(PER_HOST, per_provider=GLOBAL_CONCURRENCY)
    gate = asyncio.Semaphore(GLOBAL_CONCURRENCY)
    common = dict(
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        timeout=httpx.Timeout(25.0, connect=10.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    if TRANSPORT is not None:
        common["transport"] = TRANSPORT
    proxy = os.environ.get("WORKDAY_PROXY") or None
    async with httpx.AsyncClient(**common) as client:
        net = Net(client, limiter)
        wd_client = httpx.AsyncClient(proxy=proxy, **common) if proxy else None
        wd_net = Net(wd_client, limiter) if wd_client else net
        done = 0

        async def board(c: dict):
            nonlocal done
            use = wd_net if c["ats"] == "workday" else net
            async with gate:
                result = await _bounded(ats.FETCHERS[c["ats"]](c, use, ctx), board_label(c))
            done += 1
            if done % 500 == 0:
                print(f"  boards {done}/{len(companies)}", flush=True)
            return result

        tasks = [board(c) for c in companies] if "ats" in sources else []
        if "remote" in sources:
            tasks += [_bounded(fn(net), name) for name, fn in remote_boards.FETCHERS.items()]
        if "usajobs" in sources:
            tasks.append(_bounded(usajobs.fetch(net), "usajobs"))
        try:
            return await asyncio.gather(*tasks)
        finally:
            if wd_client is not None:
                await wd_client.aclose()


def _decision_row(job: Job, d: models.Decision) -> dict:
    return {
        "job_id": job.id, "company": job.company, "title": job.title, "location": job.location,
        "url": job.url, "decision": d.verdict, "reason_codes": d.reasons,
        "matched_evidence": d.evidence, "missing_information": d.missing,
        "classifier_version": models.CLASSIFIER_VERSION,
    }


def run(limit: int | None = None, sources: set[str] | None = None) -> dict:
    started = time.monotonic()
    sources = sources or {"ats", "remote", "usajobs"}
    cfg = filters.Config.from_dict(load_config())
    companies = load_companies()
    if limit:
        companies = companies[:limit]
    print(f"Scanning {len(companies)} company boards + remote boards + USAJOBS", flush=True)

    cache = DescCache(CACHE)
    ctx = Ctx(want=make_want(cfg), cache=cache, priority=lambda n: filters.is_priority(n, cfg))
    results = asyncio.run(fetch_everything(companies, ctx, sources))

    board_states: dict[str, str] = {}
    per_source: dict[str, Counter] = {}
    error_samples: dict[str, str] = {}
    workday_notes: Counter = Counter()
    reasons: Counter = Counter()
    kept: list[Job] = []
    seen_rejected: dict[str, str] = {}
    decisions: list[dict] = []
    rejected_candidates: list[dict] = []

    for label, fetch in results:
        src = label.split(":", 1)[0]
        tally = per_source.setdefault(src, Counter())
        if src == "workday":
            workday_notes.update(fetch.notes)
        if not fetch.ok:
            board_states[label] = store.FAILED
            tally["failed"] += 1
            error_samples.setdefault(src, fetch.error or "")
            continue
        board_states[label] = store.COMPLETE if fetch.complete else store.PARTIAL
        tally["complete" if fetch.complete else "partial"] += 1
        for job in fetch.jobs:
            job.board = label
            candidate = ctx.want(job)
            d = filters.classify(job, cfg)
            if d.verdict == models.REJECTED:
                reasons[d.reasons[0]] += 1
                # A wanted role whose description failed to load was not
                # really checked: leave its stored state alone this run.
                if candidate and job.description is None and job.source in DETAIL_SOURCES:
                    reasons["unchecked-no-description"] += 1
                    continue
                seen_rejected[job.id] = d.reasons[0]
                if candidate:
                    rejected_candidates.append(_decision_row(job, d))
            else:
                kept.append(job)
                decisions.append(_decision_row(job, d))

    groups = identity.group(kept)

    stats_path = DATA / "stats.json"
    prev_stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
    scope = scope_fingerprint(cfg)
    closing_allowed = prev_stats.get("scope") in (None, scope)

    store_path = DATA / "jobs.json"
    records = store.load(store_path)
    now = _now()
    life = store.update(records, groups, seen_rejected, board_states, now, closing_allowed)
    store_path.write_text(json.dumps(records, indent=1, sort_keys=True), encoding="utf-8")
    store.append_archive(DATA / "archive.jsonl", life.pop("archived"))

    rows = store.visible(records)
    listed = [r for r in rows if r.get("queue") == models.LISTED]
    review = [r for r in rows if r.get("queue") == models.REVIEW]
    _write_csv(rows)
    DOCS.mkdir(exist_ok=True)
    (DOCS / "jobs.json").write_text(json.dumps({"updated": now, "jobs": rows}, indent=1),
                                    encoding="utf-8")

    # Every kept role plus a stable sample of rejected candidates, so a clean
    # list can be checked for "rejected everything useful".
    rejected_candidates.sort(key=lambda r: hashlib.sha1(r["job_id"].encode()).hexdigest())
    with open(DATA / "decisions.jsonl", "w", encoding="utf-8") as f:
        for row in decisions + rejected_candidates[:DECISION_SAMPLE]:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    alerts = notify.run(rows, DATA / "notify_state.json")
    cache.save()

    stats = {
        "updated": now,
        "classifier_version": models.CLASSIFIER_VERSION,
        "scope": scope,
        "closing_allowed": closing_allowed,
        "runtime_seconds": round(time.monotonic() - started, 1),
        "listed": len(listed),
        "review": len(review),
        "by_kind": dict(Counter(r["kind"] for r in listed)),
        "by_fit": dict(Counter(r.get("schedule_fit") for r in rows)),
        "by_status": dict(Counter(r.get("status") for r in rows)),
        "lifecycle": life,
        "boards": {k: dict(v) for k, v in sorted(per_source.items())},
        "workday_outcomes": dict(workday_notes),
        "error_samples": error_samples,
        "cache": {"hits": cache.hits, "misses": cache.misses, "entries": len(cache.data)},
        "rejected": dict(reasons.most_common()),
        "alerts": alerts,
    }
    stats_path.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    render.write_readme(README, listed, len(review), stats)
    print(json.dumps({k: stats[k] for k in ("listed", "review", "by_kind", "lifecycle",
                                            "boards", "runtime_seconds")}, indent=1))
    return stats


def _write_csv(rows: list[dict]) -> None:
    cols = ["queue", "kind", "company", "title", "where", "location", "schedule_fit", "hours",
            "salary", "flags", "florida_eligibility", "category", "status", "posted_at",
            "first_seen", "url", "primary_source"]
    with open(DATA / "jobs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "flags": "; ".join(r.get("display_flags") or [])})
