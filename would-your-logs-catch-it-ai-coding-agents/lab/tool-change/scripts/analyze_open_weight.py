#!/usr/bin/env python3
"""Per-phase results of the open-weight cells (D39 points 5 and 6): which revision of q3_noop each client sent to the model.

For every cell folder with row.json and proxy.jsonl: a request "carries" a revision if q3_noop's entry in tools[] names it, or
(Qwen Code's tool search, D39 point 6) if the revision is named elsewhere in the request. Phases are bounded by the runner's own
times (row.json): phase 1 [p1_prompt_at, flip_at), phase 2 [flip_at, reopen_at or p2_end_at], phase 3 [reopen_at, p3_end_at].
A cell is valid only if phase 1 has a request to the intended model carrying A (D39 point 5).

    python3 analyze_open_weight.py ../../results/q3/open-weight-20261024 > RESULTS.json
"""
import datetime as dt
import json
import sys
from pathlib import Path

MODEL = "qwen3.8:27b-q8_0"


def ts(s):
    return dt.datetime.fromisoformat(s).timestamp() if s else None


def revisions(req):
    """The revisions of the lab tool a request carries: from tools[] first, else from the rest of the request."""
    own = {t["revision"] for t in req.get("tools") or [] if "q3_noop" in t["name"] and t["revision"]}
    if own:
        return own, "tools"
    other = req.get("elsewhere") or {}
    found = {r for r in "AB" if other.get(r)}
    return found, ("elsewhere" if found else None)


def cell(d):
    row = json.loads((d / "row.json").read_text())
    reqs = [json.loads(x) for x in (d / "proxy.jsonl").read_text().splitlines() if x.strip()]
    done = {r["id"]: r for r in reqs if r["kind"] == "done"}
    reqs = [r for r in reqs if r["kind"] == "request" and r["method"] == "POST"]
    flip = ts(row.get("flip_at"))
    bounds = {"p1": (ts(row.get("p1_prompt_at")), flip),
              "p2": (flip, ts(row.get("reopen_at")) or ts(row.get("p2_end_at"))),
              "p3": (ts(row.get("reopen_at")), ts(row.get("p3_end_at")))}
    out = {"cell": d.name, "client": row["client"], "signal": row["signal"], "flip_at": row.get("flip_at"),
           "inspect_failures": sum(r["inspect"] != "ok" for r in reqs),
           # an exchange that ended with an error: the client hanging up while the answer streamed (BrokenPipe, reset) is the
           # client's doing; anything else (upstream closed, timeout, 5xx) is the endpoint's
           "client_hung_up": sum(1 for r in reqs if any(k in ((done.get(r["id"]) or {}).get("error") or "")
                                                        for k in ("BrokenPipe", "ConnectionReset"))),
           "endpoint_errors": sum(1 for r in reqs if (lambda d: (d.get("error") and not any(
               k in d["error"] for k in ("BrokenPipe", "ConnectionReset"))) or (d.get("status") or 200) >= 500)(done.get(r["id"]) or {}))}
    for ph, (a, b) in bounds.items():
        if a is None or b is None:
            out[ph] = {"window": None}
            continue
        inside = [r for r in reqs if a <= r["t"] < b]
        mine = [r for r in inside if r["model"] == MODEL]   # revision counts and timing only over the intended model's requests
        carried = [(r, *revisions(r)) for r in mine]
        withA = [r for r, revs, _ in carried if "A" in revs]
        withB = [r for r, revs, _ in carried if "B" in revs]
        out[ph] = {"requests": len(inside), "to_intended_model": len(mine),
                   "with_A": len(withA), "with_B": len(withB),
                   "where": sorted({w for _, _, w in carried if w}),
                   "first_B_after_change_s": round(min(r["t"] for r in withB) - flip, 1) if withB and flip else None,
                   "calls": row.get(f"{ph}_calls"), "timeout": row.get(f"{ph}_timeout"),
                   "notice_seen": row.get(f"{ph}_notice_seen")}
    p1 = out["p1"]
    out["valid"] = bool(p1.get("with_A"))   # at least one phase-1 request to the intended model carrying A (D39 point 5)
    if not out["valid"]:
        out["invalid_reason"] = "no request to the intended model carrying A before the change"
    if out["p2"].get("requests") is not None and not out["p2"].get("with_B"):
        out["p2"]["B"] = "B not seen before the session closed"
    return out


def main():
    root = Path(sys.argv[1])
    plan = json.loads((root / "plan.json").read_text())
    rep = plan.get("rep", 1)   # plans written before the field existed (D39 campaign of 24 Oct) had one repetition
    expected = [f"{c['client']}__{c['signal']}__description__r{rep}" for c in plan["cells"]]
    present = sorted(d.name for d in root.iterdir() if d.is_dir())
    cells = [cell(root / n) for n in sorted(expected) if (root / n / "row.json").exists() and (root / n / "proxy.jsonl").exists()]
    json.dump({"model": MODEL, "cells": cells,
               "missing": [n for n in expected if n not in {c["cell"] for c in cells}],
               "unexpected": [n for n in present if n not in expected]}, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
