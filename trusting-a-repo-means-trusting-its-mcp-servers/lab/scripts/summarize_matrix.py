#!/usr/bin/env python3
"""Summarises one or more matrix.csv files from run_q5_matrix.py into a Markdown table:
one row per client x cell, with the number of repetitions, whether the expected prompt
was seen, the pre-selected answer, and how many repetitions started the canary before
and after the answer. Disagreements between repetitions are flagged.

  python3 scripts/summarize_matrix.py evidence/q5-run/*/matrix.csv > evidence/q5-summary.md
"""
import csv, sys
from collections import OrderedDict

def started(v):
    return v not in ("", None) and int(v) > 0

def main(paths):
    cells = OrderedDict()
    for p in paths:
        for r in csv.DictReader(open(p)):
            key = (r["client"], r["cell"])
            cells.setdefault(key, []).append(r)
    print("| Client | Cell | Reps | Prompt seen | Pre-selected | Canary before answer | Canary after answer | Versions | Agreement |")
    print("|---|---|---|---|---|---|---|---|---|")
    for (client, cell), rows in cells.items():
        n = len(rows)
        seen = sorted({r["prompt_seen"] or "—" for r in rows})
        pre = sorted({r["preselected"] for r in rows if r["preselected"]})
        before = sum(started(r["starts_before"]) for r in rows if r["starts_before"] != "")
        before_n = sum(1 for r in rows if r["starts_before"] != "")
        after_key = lambda r: r.get("starts_late") or r["starts_after"]
        after = sum(started(after_key(r)) for r in rows)
        versions = sorted({r.get("version_cell") or r["version"] for r in rows})
        expected_prompt_missing = any(x.startswith("no-") or "+no-" in x for x in seen)
        consistent = len(seen) == 1 and after in (0, n) and before in (0, before_n) and not expected_prompt_missing
        print(f"| {client} | {cell} | {n} | {', '.join(seen)} | {'; '.join(pre) or '—'} | "
              f"{f'{before}/{before_n}' if before_n else '—'} | {after}/{n} | {', '.join(versions)} | "
              f"{'consistent' if consistent else ('**prompt not reached**' if expected_prompt_missing else '**check**')} |")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1:])
