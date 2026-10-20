"""Pull the latest company registry from secnet-internships so both trackers
scan the same boards. Keeps the local copy when the download fails."""

import json
import sys
import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/ihrtkaii/secnet-internships/main/data/companies.json"
DEST = Path(__file__).resolve().parents[1] / "data" / "companies.json"

try:
    with urllib.request.urlopen(URL, timeout=30) as resp:
        rows = json.load(resp)
    if not isinstance(rows, list) or len(rows) < 100:
        raise ValueError(f"suspicious registry ({type(rows).__name__}, {len(rows)} rows)")
except Exception as exc:  # noqa: BLE001
    print(f"sync skipped, keeping local registry: {exc}")
    sys.exit(0)
DEST.write_text(json.dumps(rows, indent=2), encoding="utf-8")
print(f"synced {len(rows)} boards")
