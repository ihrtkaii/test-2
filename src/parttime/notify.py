"""Discord alerts.

Two streams:
  - immediate: a new CONFIRMED fit on the main list, one message per role
  - daily digest (first run after 8am Eastern): new possible fits, new review
    items, and material changes to anything already sent

Notification state lives in data/notify_state.json, apart from discovery
state. Each role is keyed by its store key and a fingerprint of the fields a
person would care about. State is saved after every successful send, so a
retried run does not resend what already went out.

The first run with a webhook does not dump the whole backlog: it records
everything as seen and sends one "tracker is live" message.

Set DISCORD_WEBHOOK_URL as a repo secret to turn this on.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import models

DIGEST_HOUR_ET = 8
MAX_EMBEDS = 10          # Discord's per-message limit
DIGEST_MAX_ITEMS = 40    # beyond this the digest links to the dashboard
DASHBOARD = "https://ihrtkaii.github.io/secnet-parttime/"
COLORS = {models.FIT_CONFIRMED: 0x2E9E6B, models.FIT_POSSIBLE: 0xD39B22,
          models.FIT_UNKNOWN: 0x7A8594}


def _et_now(now: datetime) -> datetime:
    # Eastern time without a tz database: DST runs 2nd Sun Mar -> 1st Sun Nov.
    y = now.year
    start = datetime(y, 3, 8, 7, tzinfo=UTC)
    start += timedelta(days=(6 - start.weekday()) % 7)
    end = datetime(y, 11, 1, 6, tzinfo=UTC)
    end += timedelta(days=(6 - end.weekday()) % 7)
    offset = -4 if start <= now < end else -5
    return now.astimezone(timezone(timedelta(hours=offset)))


def fingerprint(rec: dict) -> str:
    parts = [rec.get(k) or "" for k in ("title", "hours", "salary", "where", "schedule_fit",
                                       "queue", "kind")]
    parts.append("|".join(sorted(rec.get("flags") or [])))
    return hashlib.sha1("\x1f".join(map(str, parts)).encode()).hexdigest()[:16]


def embed(rec: dict, note: str | None = None) -> dict:
    ev = rec.get("evidence") or {}
    why = ev.get("hours") or ev.get("schedule") or ev.get("duties") or "—"
    fields = [
        {"name": "Where", "value": rec.get("where") or "—", "inline": True},
        {"name": "Hours", "value": rec.get("hours") or "not stated", "inline": True},
        {"name": "Pay", "value": rec.get("salary") or "not listed", "inline": True},
        {"name": "Schedule fit", "value": rec.get("schedule_fit") or "—", "inline": True},
        {"name": "Kind", "value": rec.get("kind") or "—", "inline": True},
        {"name": "First seen", "value": (rec.get("first_seen") or "")[:10] or "—", "inline": True},
        {"name": "Why it matched", "value": why[:1000]},
    ]
    if ev.get("experience"):
        fields.append({"name": "Experience", "value": ev["experience"][:1000]})
    flags = rec.get("display_flags") or rec.get("flags") or []
    if flags:
        fields.append({"name": "Check", "value": ", ".join(flags)[:1000]})
    title = f"{rec.get('title')} · {rec.get('company')}"
    return {
        "title": (("[changed] " if note == "changed" else "") + title)[:256],
        "url": rec.get("url") or DASHBOARD,
        "color": COLORS.get(rec.get("schedule_fit"), 0x7A8594),
        "fields": fields,
    }


class Discord:
    def __init__(self, url: str, client: httpx.Client | None = None) -> None:
        self.url = url
        self.client = client or httpx.Client(timeout=20)

    def send(self, content: str | None = None, embeds: list[dict] | None = None) -> bool:
        body = {"content": content, "embeds": embeds or []}
        for _ in range(3):
            r = self.client.post(self.url, json=body)
            if r.status_code == 429:
                try:
                    wait = float(r.json().get("retry_after", 1))
                except ValueError:
                    wait = 1.0
                time.sleep(min(wait, 10))
                continue
            return r.status_code < 300
        return False


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"seeded": False, "last_digest": None, "jobs": {}}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def run(rows: list[dict], state_path: Path, now: datetime | None = None,
        sender: Discord | None = None) -> dict:
    """Send what is due. `rows` are the visible records. Returns a summary."""
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if sender is None:
        if not url:
            return {"enabled": False}
        sender = Discord(url)
    now = now or datetime.now(UTC)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    state = load_state(state_path)
    jobs = state.setdefault("jobs", {})
    live = [r for r in rows if r.get("status") == models.OPEN]
    summary = {"enabled": True, "immediate": 0, "digest": 0, "failed": 0}

    if not state.get("seeded"):
        listed = sum(1 for r in live if r.get("queue") == models.LISTED)
        review = sum(1 for r in live if r.get("queue") == models.REVIEW)
        if sender.send(f"secnet-parttime is live: {listed} roles on the list, {review} to "
                       f"review. New confirmed fits will post here. {DASHBOARD}"):
            for r in live:
                jobs[r["key"]] = {"fp": fingerprint(r), "at": stamp}
            state["seeded"] = True
            state["last_digest"] = _et_now(now).strftime("%Y-%m-%d")
            save_state(state_path, state)
            summary["seeded"] = True
        else:
            summary["failed"] += 1
        return summary

    # Immediate: brand-new confirmed fits.
    for r in live:
        if (r.get("queue") == models.LISTED and r.get("schedule_fit") == models.FIT_CONFIRMED
                and r["key"] not in jobs):
            if sender.send("New confirmed fit", [embed(r)]):
                jobs[r["key"]] = {"fp": fingerprint(r), "at": stamp}
                save_state(state_path, state)
                summary["immediate"] += 1
            else:
                summary["failed"] += 1

    # Daily digest.
    et = _et_now(now)
    today = et.strftime("%Y-%m-%d")
    if state.get("last_digest") != today and et.hour >= DIGEST_HOUR_ET:
        due: list[tuple[dict, str | None]] = []
        for r in live:
            prev = jobs.get(r["key"])
            if prev is None:
                due.append((r, None))
            elif prev.get("fp") != fingerprint(r):
                due.append((r, "changed"))
        if due:
            due.sort(key=lambda x: (x[0].get("queue") != models.LISTED, x[1] is not None))
            shown = due[:DIGEST_MAX_ITEMS]
            new_listed = sum(1 for r, n in due if n is None and r.get("queue") == models.LISTED)
            new_review = sum(1 for r, n in due if n is None and r.get("queue") == models.REVIEW)
            changed = sum(1 for _, n in due if n)
            head = (f"Daily digest: {new_listed} new on the list, {new_review} new to review, "
                    f"{changed} changed.")
            if len(due) > len(shown):
                head += f" Showing {len(shown)}; the rest are on the dashboard: {DASHBOARD}"
            ok = True
            for i in range(0, len(shown), MAX_EMBEDS):
                chunk = shown[i:i + MAX_EMBEDS]
                if not sender.send(head if i == 0 else None, [embed(r, n) for r, n in chunk]):
                    ok = False
                    summary["failed"] += 1
                    break
                for r, _ in chunk:
                    jobs[r["key"]] = {"fp": fingerprint(r), "at": stamp}
                save_state(state_path, state)
                summary["digest"] += len(chunk)
            if ok:
                for r, _ in due[len(shown):]:  # counted in the header; don't repeat tomorrow
                    jobs[r["key"]] = {"fp": fingerprint(r), "at": stamp}
                state["last_digest"] = today
        else:
            state["last_digest"] = today
        save_state(state_path, state)

    # Forget roles that are gone so a re-post later counts as new.
    keys = {r["key"] for r in rows}
    for k in [k for k in jobs if k not in keys]:
        del jobs[k]
    save_state(state_path, state)
    return summary
