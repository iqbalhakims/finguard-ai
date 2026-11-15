"""CI quality gate for the trained champion model.

Fails (exit 1) when the held-out test metrics fall below the minimums, so a
regressed model never gets packaged into an image. When running in GitHub
Actions it also writes a metrics table to the job summary.

    python scripts/check_model.py --min-pr-auc 0.75 --min-precision 0.80
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-dir", type=Path, default=Path("artifacts/model"))
    parser.add_argument("--min-pr-auc", type=float, default=0.75)
    parser.add_argument("--min-precision", type=float, default=0.80)
    parser.add_argument("--min-recall", type=float, default=0.50)
    args = parser.parse_args()

    meta = json.loads((args.model_dir / "metadata.json").read_text())
    test = meta["metrics"]["test"]
    checks = [
        ("PR-AUC", test["pr_auc"], args.min_pr_auc),
        ("Precision", test["precision"], args.min_precision),
        ("Recall", test["recall"], args.min_recall),
    ]
    failed = [name for name, value, minimum in checks if value < minimum]

    lines = [
        f"### Model quality gate: {'❌ failed' if failed else '✅ passed'}",
        "",
        f"Champion **{meta['model_type']}**, review threshold {meta['threshold']:.4f}, "
        f"block threshold {meta['block_threshold']:.4f}",
        "",
        "| Test metric | Value | Minimum | Result |",
        "|---|---|---|---|",
        *[
            f"| {name} | {value:.4f} | {minimum:.2f} | {'❌' if value < minimum else '✅'} |"
            for name, value, minimum in checks
        ],
        f"| Fraud amount recall | {test.get('fraud_amount_recall', float('nan')):.4f} | – | – |",
        f"| Precision @ 80% recall | {test['precision_at_recall_80']:.4f} | – | – |",
    ]
    report = "\n".join(lines)
    print(report)
    if summary := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as fh:
            fh.write(report + "\n")

    if failed:
        print(f"\nquality gate failed on: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
