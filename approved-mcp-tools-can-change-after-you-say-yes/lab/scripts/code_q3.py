#!/usr/bin/env python3
"""Rule-based provisional coder for Q3 repetitions (preregistration deviations D12 and D16 point 7).

Input: one repetition directory written by run_q3_cell.py (row.json + q3-server.log).
Output: one outcome per phase, with the facts it was derived from.

Outcome categories, in order of precedence (D12 point 3):
  1 refused-or-disconnected  no successful call after the change and the server was shut down by the client
  2 re-approval-required     the client asked for permission again for the changed tool
  3 notified                 the client showed a change message without asking for permission again
  4 silent-accept            B was served by a tools/list after the change, the call succeeded, nothing shown
  5 stale                    the call succeeded but no tools/list was served after the change (A still in use)
  6 error-no-call-timeout    no successful call after the change
A repetition whose phase 1 did not produce a successful call with definition A is "invalid-setup".
This coder is provisional: the two preregistered coders classify the captures independently of it.

  python3 code_q3.py DIR [DIR ...] [--csv out.csv]
"""
import argparse, csv, json, sys
from pathlib import Path

ENDS = {"stdin-closed", "sigterm", "sigint"}

def load(d):
    d = Path(d)
    row = json.loads((d / "row.json").read_text())
    ev = []
    for line in (d / "q3-server.log").read_text().splitlines():
        try:
            ev.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return row, ev

def window(ev, start, end):
    return [e for e in ev if start <= e.get("ts", "") and (end is None or e.get("ts", "") < end)]

def facts(ev, start, end):
    w = window(ev, start, end)
    calls = [e for e in w if e.get("event") == "tools/call"]
    ok_calls = [e for e in calls if e.get("known")]
    lists = [e for e in w if e.get("event") == "served-list"]
    alive_at_start = {e["pid"] for e in ev if e.get("event") == "start" and e.get("ts", "") < start}
    ended_before = {e["pid"] for e in ev if e.get("event") in ENDS and e.get("ts", "") < start}
    alive_at_start -= ended_before
    ended = {e["pid"] for e in w if e.get("event") in ENDS}
    started = {e["pid"] for e in w if e.get("event") == "start"}
    return {
        "calls": len(calls),
        "ok_calls": len(ok_calls),
        "ok_call_states": sorted({e.get("served_state") for e in ok_calls}),
        "lists_served": [e.get("served") for e in lists],
        "served_b": any(e.get("served") == "B" for e in lists),
        "servers_started": len(started),
        "servers_ended": len(ended),
        "all_servers_ended": bool(alive_at_start | started) and (alive_at_start | started) <= ended,
        "signal_delivered": [x for e in w if e.get("event") == "signal-delivered" for x in e.get("delivered", [])],
        "signal_not_deliverable": any(e.get("event") == "signal-not-deliverable" for e in w),
        "eras": sorted({"legacy" for e in ev if e.get("event") == "legacy-initialize"} |
                       {"modern" for e in ev if e.get("event") == "modern-request"}),
    }

def outcome(f, approvals, notice_seen):
    if f["ok_calls"] == 0 and f["all_servers_ended"]:
        return 1, "refused-or-disconnected"
    if approvals > 0:
        return 2, "re-approval-required"
    if notice_seen:
        return 3, "notified"
    if f["ok_calls"] > 0 and f["served_b"]:
        return 4, "silent-accept"
    if f["ok_calls"] > 0:
        return 5, "stale"
    return 6, "error-no-call-timeout"

def code(d):
    row, ev = load(d)
    # Each phase window ends when the phase ended (p*_end_at), before the runner closes the client,
    # so that the runner's own shutdown of the server is never read as the client disconnecting it.
    flip, reopen, end = row.get("flip_at"), row.get("reopen_at"), row.get("t_end")
    p2_end = row.get("p2_end_at") or reopen or end
    p3_end = row.get("p3_end_at") or end
    p1 = facts(ev, row.get("t_start", ""), flip)
    out = {"dir": str(d), "client": row["client"], "signal": row["signal"], "change": row["change"], "rep": row["rep"],
           "version_before": row.get("version_before"), "version_after": row.get("version_after"),
           "p1_ok": p1["ok_calls"] > 0 and p1["ok_call_states"] == ["A"], "p1_approvals": row.get("p1_approvals", 0),
           "eras": "+".join(p1["eras"] or ["none"])}
    if not out["p1_ok"]:
        out.update({"p2_outcome": "invalid-setup", "p2_code": 0})
        return out
    p2 = facts(ev, flip, p2_end)
    c, name = outcome(p2, row.get("p2_approvals", 0), row.get("p2_notice_seen", False))
    if row.get("p2_classifier_unavailable"):
        # D19: the question came from the client's own classifier being unavailable, not from the change
        c, name = 0, "invalid-external"
    if row["signal"] == "list_changed":
        delivered = "yes" if p2["signal_delivered"] else ("no" if p2["signal_not_deliverable"] else "unknown")
    else:
        delivered = "n-a"
    out.update({"p2_code": c, "p2_outcome": name, "p2_signal_delivered": delivered,
                "p2_channels": "+".join(sorted({x.get("era", "?") for x in p2["signal_delivered"]})),
                "p2_lists_served": "".join(x or "?" for x in p2["lists_served"]), "p2_ok_calls": p2["ok_calls"],
                "p2_servers_started": p2["servers_started"], "p2_approvals": row.get("p2_approvals", 0),
                "p2_notice_seen": row.get("p2_notice_seen", False)})
    if reopen:
        p3 = facts(ev, reopen, p3_end)
        c3, name3 = outcome(p3, row.get("p3_approvals", 0), row.get("p3_notice_seen", False))
        out.update({"p3_code": c3, "p3_outcome": name3, "p3_lists_served": "".join(x or "?" for x in p3["lists_served"]),
                    "p3_approvals": row.get("p3_approvals", 0)})
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--csv")
    a = ap.parse_args()
    rows = [code(d) for d in a.dirs]
    if a.csv:
        fields = sorted({k for r in rows for k in r}, key=lambda k: (k != "dir", k))
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    for r in rows:
        print(json.dumps(r))

if __name__ == "__main__":
    sys.exit(main())
