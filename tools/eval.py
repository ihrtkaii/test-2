"""Measure the classifier against hand-labeled postings.

    python tools/eval.py                 # data/labeled.jsonl, or the examples
    python tools/eval.py path/to/set.jsonl

Labels: fit (should be on the main list), review (worth checking),
reject (should not appear). Prints:

  precision   of the roles shown on the main list, how many are labeled fit
  recall      of the roles labeled fit, how many reached the main list
  reach       of fit + review labels, how many appeared anywhere (list or queue)

and every disagreement, with the reason codes and evidence, so a rule change
can be compared against the same set. This measures the labeled sample only:
it cannot estimate how many roles the whole job market hides from the
tracker.
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from parttime import filters, models  # noqa: E402
from parttime.models import Job  # noqa: E402

EXAMPLES = ROOT / "tests" / "labeled_examples.jsonl"


def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "labeled.jsonl"
    if not path.exists():
        print(f"{path} not found; using {EXAMPLES.relative_to(ROOT)} (synthetic examples)")
        path = EXAMPLES
    cfg = filters.Config.from_dict(json.loads((ROOT / "data" / "config.json").read_text()))
    rows = load(path)
    matrix: Counter = Counter()
    misses = []
    to_label = {models.LISTED: "fit", models.REVIEW: "review", models.REJECTED: "reject"}
    for row in rows:
        fields = {k: v for k, v in row["job"].items() if k in Job.__dataclass_fields__}
        fields.setdefault("url", "")
        job = Job(**fields)
        d = filters.classify(job, cfg)
        got = to_label[d.verdict]
        matrix[(row["label"], got)] += 1
        if got != row["label"]:
            misses.append((row, d))
    shown = sum(v for (lab, got), v in matrix.items() if got == "fit")
    fit_shown = matrix[("fit", "fit")]
    fit_total = sum(v for (lab, _), v in matrix.items() if lab == "fit")
    wanted = sum(v for (lab, _), v in matrix.items() if lab in ("fit", "review"))
    reached = sum(v for (lab, got), v in matrix.items()
                  if lab in ("fit", "review") and got in ("fit", "review"))

    def pct(a, b):
        return f"{a}/{b} = {100 * a / b:.0f}%" if b else "n/a"

    print(f"classifier {models.CLASSIFIER_VERSION} on {len(rows)} labeled roles ({path.name})")
    print(f"precision  {pct(fit_shown, shown)}")
    print(f"recall     {pct(fit_shown, fit_total)}")
    print(f"reach      {pct(reached, wanted)}")
    print("\nlabel -> got")
    for (lab, got), v in sorted(matrix.items()):
        print(f"  {lab:6} -> {got:6} {v}")
    if misses:
        print("\ndisagreements:")
        for row, d in misses:
            j = row["job"]
            print(f"  [{row['label']} -> {to_label[d.verdict]}] {j['title']} @ {j['company']}")
            print(f"      reasons: {', '.join(d.reasons)}")
            for k, v in d.evidence.items():
                print(f"      {k}: {v}")
            if row.get("note"):
                print(f"      note: {row['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
