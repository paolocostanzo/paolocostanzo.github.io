#!/usr/bin/env python3
"""Scoring of detection rules for RQ5 (D6, D13 point 3, D24 point 5), independent of how a rule is written.

Input: for each rule, the times (Unix seconds) at which it fired, per event run and per holdout session.
  - Event run: the rule detects the event if it fires between the ground-truth time of the event and the end of the run (the
    moment the logs were copied). Hits before the event do not count. Runs whose ground truth shows no event are left out
    ("no event", D24 point 2). Since D27 point 10 a run whose export failed is retried and never completed, so the status
    "export-failed" can only come from inputs made before that change; such runs are left out with their reason.
  - Holdout: the session [t0, t1] is cut into one-minute windows from t0; a trailing window shorter than 60 s is dropped. A
    window is a false-positive window if the rule fires at least once in it. Reported: count, share, Wilson 95% interval, and
    alerts per hour (hits per hour of holdout, and false-positive windows per hour).
  - "Tells the event apart" (D6, D27 point 2): fires on every scored event run and on fewer than 5% of holdout windows (k/n < 0.05,
    the preregistered "below 5%"; D6 had written "at most"). Observed rate on the holdout, not a population estimate (D13 point 3);
    consecutive windows are autocorrelated.

  python3 score.py SCORES_INPUT.json > SCORES.json
SCORES_INPUT.json: {"rules": {"<id>": {"event_runs": [{"run": "...", "event_time": T, "end": T, "status": "ok|no-event|export-failed",
                                                      "hits": [T, ...]}],
                                     "holdout": [{"session": "...", "t0": T, "t1": T, "hits": [T, ...]}]}}}
"""
import json, math, sys

WINDOW = 60
THRESHOLD = 0.05


def wilson(k, n, z=1.959964):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def windows(t0, t1, size=WINDOW):
    n = int((t1 - t0) // size)
    return [(t0 + i * size, t0 + (i + 1) * size) for i in range(n)]


def score_events(runs):
    scored = [r for r in runs if r.get("status", "ok") == "ok"]
    detected = [r for r in scored if any(r["event_time"] <= h <= r["end"] for h in r["hits"])]
    latency = [min(h for h in r["hits"] if r["event_time"] <= h <= r["end"]) - r["event_time"] for r in detected]
    return {"scored": len(scored), "detected": len(detected),
            "left_out": {s: sum(r.get("status") == s for r in runs) for s in ("no-event", "export-failed")},
            "missed": [r["run"] for r in scored if r not in detected],
            "latency_s": {"min": min(latency), "max": max(latency)} if latency else None}


def score_holdout(sessions):
    """The threshold is on all windows (D27 point 3). Coverage (D31 point 2, D36): windows holding at least one record of the
    rule's source, per session as scores_input.py gives it; the share over covered windows is reported next to it, never used
    for the threshold. A session without a coverage figure makes the total unknown (None)."""
    n_win = fp = hits_in = 0
    seconds = 0
    for s in sessions:
        ws = windows(s["t0"], s["t1"])
        n_win += len(ws)
        seconds += len(ws) * WINDOW
        fp += sum(any(a <= h < b for h in s["hits"]) for a, b in ws)
        hits_in += sum(1 for h in s["hits"] if ws and ws[0][0] <= h < ws[-1][1])
    hours = seconds / 3600
    covs = [s.get("covered_windows") for s in sessions]
    covered = sum(covs) if sessions and all(c is not None for c in covs) else None
    return {"windows": n_win, "fp_windows": fp, "fp_share": (fp / n_win) if n_win else None, "wilson95": wilson(fp, n_win),
            "hours": hours, "hits_per_hour": (hits_in / hours) if hours else None, "fp_windows_per_hour": (fp / hours) if hours else None,
            "covered_windows": covered, "fp_share_covered": (fp / covered) if covered else None,
            "per_session": [{"session": s.get("session"), "windows": len(windows(s["t0"], s["t1"])),
                             "covered_windows": s.get("covered_windows")} for s in sessions]}


def score(rules):
    out = {}
    for rid, r in rules.items():
        ev, ho = score_events(r["event_runs"]), score_holdout(r["holdout"])
        out[rid] = {"events": ev, "holdout": ho,
                    "tells_apart": bool(ev["scored"]) and ev["detected"] == ev["scored"] and ho["fp_share"] is not None
                    and 20 * ho["fp_windows"] < ho["windows"]}  # k/n < 0.05 = THRESHOLD, in integers
    return out


if __name__ == "__main__":
    json.dump(score(json.load(open(sys.argv[1]))["rules"]), sys.stdout, indent=1)
