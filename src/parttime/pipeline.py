"""One run: fetch every source, classify, merge with what's stored, publish."""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from . import filters, render
from .models import Fetch, Job
from .net import HostLimiter, Net
from .sources import ats, remote_boards, usajobs

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DOCS = ROOT / "docs"
README = ROOT / "README.md"

GLOBAL_CONCURRENCY = 32
PER_HOST = 8
BOARD_TIMEOUT = 150
KEEP_CLOSED_DAYS = 21
TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests swap in a MockTransport
USER_AGENT = "secnet-parttime/1.0 (+https://github.com/ihrtkaii/secnet-parttime)"


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


def make_want(local: tuple[str, ...]):
    """Cheap pre-check deciding which roles are worth a description fetch."""
    vague = re.compile(r"^(—|-|)$|\d+\s+locations?|multiple", re.IGNORECASE)

    def want(job: Job) -> bool:
        if not filters.is_security(job.title) or filters.is_senior(job.title):
            return False
        return filters.where(job, local) is not None or bool(vague.search(job.location.strip()))
    return want


async def _bounded(coro, label: str) -> tuple[str, Fetch]:
    try:
        async with asyncio.timeout(BOARD_TIMEOUT):
            return label, await coro
    except Exception as exc:  # noqa: BLE001 - one bad board never sinks the run
        return label, Fetch([], ok=False, error=f"{type(exc).__name__}: {exc}"[:200])


async def fetch_everything(companies: list[dict], want, sources: set[str]):
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
                label = f"{c['ats']}:{c['slug']}" + (f":{c['site']}" if c.get("site") else "")
                result = await _bounded(ats.FETCHERS[c["ats"]](c, use, want), label)
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


def _dedupe(jobs: list[Job]) -> list[Job]:
    """One row per (company, title, place). Remote boards re-post ATS roles."""
    best: dict[tuple, Job] = {}
    for j in jobs:
        key = (re.sub(r"\W+", "", j.company.lower()), re.sub(r"\W+", "", j.title.lower()),
               j.where)
        prior = best.get(key)
        if prior is None or (prior.source in remote_boards.FETCHERS and
                             j.source not in remote_boards.FETCHERS):
            best[key] = j
    return list(best.values())


def merge(stored: dict, kept: list[Job], ok_boards: set[str], now: str) -> dict:
    out = dict(stored)
    seen = set()
    for j in kept:
        rec = j.record()
        prior = out.get(j.id) or {}
        rec["first_seen"] = prior.get("first_seen") or now
        rec["last_seen"] = now
        rec["status"] = "open"
        out[j.id] = rec
        seen.add(j.id)
    cutoff = (datetime.now(UTC) - timedelta(days=KEEP_CLOSED_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for jid, rec in list(out.items()):
        if jid in seen:
            continue
        if rec.get("status") == "open" and rec.get("board") in ok_boards:
            rec["status"] = "closed"
            rec["closed_at"] = now
        if rec.get("status") == "closed" and (rec.get("closed_at") or "") < cutoff:
            del out[jid]
    return out


def run(limit: int | None = None, sources: set[str] | None = None) -> dict:
    sources = sources or {"ats", "remote", "usajobs"}
    cfg = load_config()
    local = tuple(x.lower() for x in cfg.get("local_places", filters.DEFAULT_LOCAL))
    companies = load_companies()
    if limit:
        companies = companies[:limit]
    print(f"Scanning {len(companies)} company boards + remote boards + USAJOBS", flush=True)

    results = asyncio.run(fetch_everything(companies, make_want(local), sources))

    ok_boards: set[str] = set()
    per_source = Counter()
    errors_by_source = Counter()
    error_samples: dict[str, str] = {}
    reasons = Counter()
    kept: list[Job] = []
    for label, fetch in results:
        src = label.split(":", 1)[0]
        if not fetch.ok:
            errors_by_source[src] += 1
            error_samples.setdefault(src, fetch.error or "")
            continue
        per_source[src] += 1
        ok_boards |= {j.board for j in fetch.jobs} | {label}
        for job in fetch.jobs:
            why = filters.classify(job, local)
            if why:
                reasons[why] += 1
            else:
                kept.append(job)
    kept = _dedupe(kept)

    store_path = DATA / "jobs.json"
    stored = json.loads(store_path.read_text(encoding="utf-8")) if store_path.exists() else {}
    now = _now()
    merged = merge(stored, kept, ok_boards, now)
    store_path.write_text(json.dumps(merged, indent=1, sort_keys=True), encoding="utf-8")

    open_rows = sorted((r for r in merged.values() if r.get("status") == "open"),
                       key=lambda r: (r.get("posted_at") or r.get("first_seen") or ""),
                       reverse=True)
    _write_csv(open_rows)
    DOCS.mkdir(exist_ok=True)
    (DOCS / "jobs.json").write_text(json.dumps({"updated": now, "jobs": open_rows}, indent=1),
                                    encoding="utf-8")
    stats = {
        "updated": now,
        "open": len(open_rows),
        "new_this_run": sum(1 for r in open_rows if r.get("first_seen") == now),
        "by_kind": Counter(r["kind"] for r in open_rows),
        "boards_ok": dict(per_source),
        "boards_failed": dict(errors_by_source),
        "error_samples": error_samples,
        "rejected": dict(reasons.most_common()),
    }
    (DATA / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    render.write_readme(README, open_rows, stats)
    print(json.dumps({k: stats[k] for k in ("open", "new_this_run", "by_kind",
                                            "boards_ok", "boards_failed")}, indent=1))
    return stats


def _write_csv(rows: list[dict]) -> None:
    cols = ["kind", "company", "title", "where", "location", "hours", "salary", "flags",
            "category", "posted_at", "first_seen", "url", "source"]
    with open(DATA / "jobs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "flags": "; ".join(r.get("flags") or [])})
