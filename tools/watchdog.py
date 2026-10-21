"""Fail loudly when the tracker stops updating.

GitHub can delay or drop scheduled runs, and a broken run fails quietly in
the Actions tab. This runs on its own daily schedule, reads data/stats.json,
and if the last successful update is older than MAX_AGE_HOURS it posts to
Discord (when DISCORD_WEBHOOK_URL is set) and exits non-zero, which makes
GitHub email the repo owner about the failed workflow.
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

MAX_AGE_HOURS = 18
STATS = Path(__file__).resolve().parents[1] / "data" / "stats.json"


def main() -> int:
    try:
        stats = json.loads(STATS.read_text(encoding="utf-8"))
        updated = datetime.strptime(stats["updated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (OSError, ValueError, KeyError) as exc:
        problem = f"cannot read data/stats.json ({exc})"
    else:
        age = (datetime.now(UTC) - updated).total_seconds() / 3600
        if age <= MAX_AGE_HOURS:
            print(f"ok: last update {age:.1f}h ago")
            return 0
        problem = f"last successful update was {age:.0f} hours ago"
    message = f"secnet-parttime watchdog: {problem}. Check the Actions tab."
    print(message)
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if url:
        try:
            httpx.post(url, json={"content": message}, timeout=20)
        except httpx.HTTPError as exc:
            print(f"discord post failed: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
