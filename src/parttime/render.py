"""README tables. The prose around them lives in README.md itself; only the
block between the LIST markers is rewritten each run. The README shows the
main list; the review queue lives on the dashboard."""

from __future__ import annotations

import re
from pathlib import Path

from .models import FIT_CONFIRMED, FIT_POSSIBLE, KINDS

START, END = "<!-- LIST:START -->", "<!-- LIST:END -->"
MAX_ROWS = 150  # per section; the dashboard shows everything
DASHBOARD = "https://ihrtkaii.github.io/test-2/"
FIT_LABEL = {FIT_CONFIRMED: "Confirmed", FIT_POSSIBLE: "Possible"}


def _cell(text) -> str:
    return str(text or "—").replace("|", "/").replace("\n", " ").strip() or "—"


def _row(r: dict) -> str:
    flags = ", ".join(r.get("display_flags") or r.get("flags") or []) or "—"
    posted = (r.get("posted_at") or r.get("first_seen") or "")[:10] or "—"
    return (f"| {_cell(r.get('company'))} | {_cell(r.get('title'))} | {_cell(r.get('where'))} "
            f"| {_cell(r.get('hours'))} | {FIT_LABEL.get(r.get('schedule_fit'), '—')} "
            f"| {_cell(r.get('salary'))} | {flags} | {posted} | [Apply]({r.get('url')}) |")


def block(rows: list[dict], review_count: int, stats: dict) -> str:
    out = [START, "",
           f"**{len(rows)} roles on the list**, plus {review_count} worth checking on the "
           f"[dashboard]({DASHBOARD}) · updated {stats['updated'][:16].replace('T', ' ')} UTC", ""]
    for kind in KINDS:
        section = [r for r in rows if r.get("kind") == kind]
        out += [f"## {kind} ({len(section)})", ""]
        if not section:
            out += ["_Nothing open right now._", ""]
            continue
        out += ["| Company | Role | Where | Hours | Fit | Pay | Check | Posted | Link |",
                "|---|---|---|---|---|---|---|---|---|"]
        out += [_row(r) for r in section[:MAX_ROWS]]
        if len(section) > MAX_ROWS:
            out.append(f"\n_{len(section) - MAX_ROWS} more on the dashboard._")
        out.append("")
    out.append(END)
    return "\n".join(out)


def write_readme(path: Path, rows: list[dict], review_count: int, stats: dict) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else f"{START}\n{END}\n"
    if START not in text:
        text += f"\n{START}\n{END}\n"
    new = re.sub(re.escape(START) + r".*?" + re.escape(END),
                 lambda _: block(rows, review_count, stats), text, flags=re.DOTALL)
    path.write_text(new, encoding="utf-8")
