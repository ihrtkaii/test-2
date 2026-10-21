"""Add a real posting to the labeled evaluation set.

    python tools/label.py <job_id> fit|review|reject ["optional note"]

Takes the role's fields from data/jobs.json or data/decisions.jsonl and its
description from the local cache (data/cache/), so run the tracker locally
first. Appends one line to data/labeled.jsonl.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from parttime.cache import DescCache  # noqa: E402

FIELDS = ("id", "source", "company", "title", "location", "url", "employment_type",
          "remote", "hybrid")


def find(job_id: str) -> dict | None:
    store = ROOT / "data" / "jobs.json"
    if store.exists():
        for rec in json.loads(store.read_text(encoding="utf-8")).values():
            if job_id == rec.get("id") or job_id in (rec.get("source_job_ids") or []):
                return {k: rec.get(k) for k in FIELDS}
    log = ROOT / "data" / "decisions.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["job_id"] == job_id:
                return {"id": job_id, "source": job_id.split(":")[0], "company": row["company"],
                        "title": row["title"], "location": row.get("location", ""),
                        "url": row["url"]}
    return None


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[2] not in ("fit", "review", "reject"):
        print(__doc__)
        return 2
    job_id, label = sys.argv[1], sys.argv[2]
    note = " ".join(sys.argv[3:])
    job = find(job_id)
    if job is None:
        print(f"{job_id} not found in data/jobs.json or data/decisions.jsonl")
        return 1
    text = DescCache(ROOT / "data" / "cache" / "descriptions.json.gz").text(job_id)
    if text is None:
        print("warning: no cached description; the label will be judged on title/location only")
    job["description"] = text
    with open(ROOT / "data" / "labeled.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"job": job, "label": label, "note": note}) + "\n")
    print(f"labeled {job_id} as {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
