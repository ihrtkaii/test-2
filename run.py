"""Run the tracker.

    python run.py                      # everything
    python run.py --limit 50           # first 50 company boards (quick test)
    python run.py --sources remote,usajobs
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from parttime import pipeline  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only scan the first N company boards")
    ap.add_argument("--sources", default="ats,remote,usajobs",
                    help="comma list from: ats, remote, usajobs")
    args = ap.parse_args()
    pipeline.run(limit=args.limit, sources=set(args.sources.split(",")))
