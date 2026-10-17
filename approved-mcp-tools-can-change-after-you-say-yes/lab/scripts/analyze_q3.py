#!/usr/bin/env python3
"""Analysis of the Q3 primary matrix (RQ3/H3), as fixed by the preregistration and deviations D7, D12 and D16.

Input: the output folder of q3_host.py (one sub-folder per repetition, each with row.json and q3-server.log).
Steps: code every repetition with code_q3.py (provisional rule-based coder); write matrix.csv; then
  - per client x signal: counts of the phase-2 outcome (always reported, D12 point 4);
  - H3 first clause: a client's outcome is the modal phase-2 outcome over its primary repetitions (both signal levels);
    a tie is "mixed" and counts against the clause only if one of the tied outcomes is re-approval (D7);
    the clause holds if at most one client's outcome is re-approval required;
  - H3 second clause: on list_changed repetitions where the signal was delivered (D16 point 1), a client "acts on the
    notification" if it notifies or asks for re-approval in at least 1 of its delivered repetitions (D12 point 4);
    the clause holds if no client acts; clients with no delivered repetition are reported as "signal not deliverable".
Invalid repetitions (first call did not succeed with definition A) are reported and excluded. The reopen phase (p3) is
exploratory and only tabulated. These are the rule-based codes: the two preregistered coders classify independently.

  python3 analyze_q3.py RESULTS_DIR [--out RESULTS_DIR/analysis]
"""
import argparse, collections, csv, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import code_q3  # noqa: E402

REAPPROVAL, NOTIFIED = "re-approval-required", "notified"

def modal(outcomes):
    c = collections.Counter(outcomes)
    if not c:
        return "no-valid-repetition", []
    top = max(c.values())
    winners = sorted(k for k, v in c.items() if v == top)
    return (winners[0] if len(winners) == 1 else "mixed"), winners

def analyze(rows):
    INVALID = ("invalid-setup", "invalid-external")  # D19: invalid-external = the client's own classifier was unavailable
    valid = [r for r in rows if r.get("p2_outcome") not in INVALID]
    invalid = [r for r in rows if r.get("p2_outcome") in INVALID]
    clients = sorted({r["client"] for r in rows})
    cells = {}
    for r in valid:
        cells.setdefault(f'{r["client"]}|{r["signal"]}', collections.Counter())[r["p2_outcome"]] += 1
    clause1 = {}
    for c in clients:
        out, winners = modal([r["p2_outcome"] for r in valid if r["client"] == c])
        counts_against = out == REAPPROVAL or (out == "mixed" and REAPPROVAL in winners)
        clause1[c] = {"modal": out, "tied": winners if out == "mixed" else [], "counts_as_reapproval": counts_against,
                      "n_valid": sum(1 for r in valid if r["client"] == c)}
    n_reapproval = sum(1 for v in clause1.values() if v["counts_as_reapproval"])
    clause2 = {}
    for c in clients:
        lc = [r for r in valid if r["client"] == c and r["signal"] == "list_changed"]
        delivered = [r for r in lc if r.get("p2_signal_delivered") == "yes"]
        acts = [r for r in delivered if r["p2_outcome"] in (REAPPROVAL, NOTIFIED)]
        clause2[c] = {"list_changed_valid": len(lc), "delivered": len(delivered),
                      "not_deliverable": sum(1 for r in lc if r.get("p2_signal_delivered") == "no"),
                      "acts_on_notification": (len(acts) >= 1) if delivered else None, "acting_repetitions": len(acts),
                      "channels": sorted({r.get("p2_channels") for r in delivered if r.get("p2_channels")})}
    acting = [c for c, v in clause2.items() if v["acts_on_notification"]]
    p3 = {}
    for r in valid:
        if r.get("p3_outcome"):
            p3.setdefault(r["client"], collections.Counter())[r["p3_outcome"]] += 1
    return {
        "n_repetitions": len(rows), "n_valid": len(valid),
        "invalid": [f'{r["client"]}|{r["signal"]}|r{r["rep"]}|{r["p2_outcome"]}' for r in invalid],
        "cells_phase2": {k: dict(v) for k, v in sorted(cells.items())},
        "H3_clause1": {"per_client": clause1, "clients_reapproval": n_reapproval, "holds": n_reapproval <= 1},
        "H3_clause2": {"per_client": clause2, "clients_acting": acting,
                       "testable_clients": [c for c, v in clause2.items() if v["delivered"]], "holds": not acting},
        "reopen_phase3_exploratory": {k: dict(v) for k, v in sorted(p3.items())},
        "eras": {c: sorted({r.get("eras") for r in valid if r["client"] == c}) for c in clients},
        "versions": {c: sorted({r.get("version_before") for r in rows if r["client"] == c} |
                               {r.get("version_after") for r in rows if r["client"] == c}) for c in clients},
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--out")
    a = ap.parse_args()
    res = Path(a.results)
    out = Path(a.out) if a.out else res / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    rows = [code_q3.code(d) for d in sorted(p.parent for p in res.glob("*/row.json"))]
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k != "dir", k))
    with open(out / "matrix.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    result = analyze(rows)
    (out / "analysis.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
