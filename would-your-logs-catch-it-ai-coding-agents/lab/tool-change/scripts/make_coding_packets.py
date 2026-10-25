#!/usr/bin/env python3
"""Builds one blind coding packet per Q3 repetition for the two preregistered coders (author; local model): the screens of
phase 2 (after the change) and the facts in the server log for that window. The rule-based outcome is NOT included.
Packet ids are shuffled with a seed, so that order does not reveal client or repetition; the key is written separately.

  python3 make_coding_packets.py RESULTS_DIR OUT_DIR --seed 20261015
writes OUT_DIR/packets/<id>.md, OUT_DIR/coding-sheet.csv (id, outcome, note — to fill) and OUT_DIR/key.csv (id -> repetition).
"""
import argparse, csv, json, random, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import code_q3  # noqa: E402

INSTRUCTIONS = """## How to code (D12, with precedence: the lowest number that applies wins)
1 refused-or-disconnected — no successful call after the change, and the client shut the server down
2 re-approval-required — the client asked for permission again for the changed tool
3 notified — the client showed a message that the server's tools changed, without asking for permission again
4 silent-accept — the server served definition B after the change, the call succeeded, nothing was shown
5 stale — the call succeeded but the client never asked for the tool list again after the change (still using A)
6 error-no-call-timeout — no successful call after the change
Code only from this packet. Answer with one label."""

def tail(text, n=30):
    lines = [l.rstrip() for l in text.splitlines() if l.strip() and not set(l.strip()) <= set("▄▀─━═│ ")]
    return "\n".join(lines[-n:])

def packet(d):
    d = Path(d)
    row, ev = code_q3.load(d)
    flip, end = row.get("flip_at"), row.get("p2_end_at") or row.get("reopen_at") or row.get("t_end")
    f = code_q3.facts(ev, flip, end)
    screens = []
    for name in row.get("captures", []):
        if "-p2-" in name:
            screens.append(f"### Screen: {name.split('__')[-1].removesuffix('.txt')}\n```\n{tail((d / name).read_text())}\n```")
    facts = [
        f"- tools/list served after the change: {', '.join(x or '?' for x in f['lists_served']) or 'none'}",
        f"- successful tool calls after the change: {f['ok_calls']} (definition in force at the call: {', '.join(f['ok_call_states']) or '-'})",
        f"- server processes started / ended in the window: {f['servers_started']} / {f['servers_ended']}"
        + (" (all server processes ended)" if f["all_servers_ended"] else ""),
        f"- change signal: {row['signal']}" + (f"; delivered: {'yes' if f['signal_delivered'] else ('no' if f['signal_not_deliverable'] else 'unknown')}"
                                              if row["signal"] == "list_changed" else ""),
        f"- permission questions detected by the runner after the change: {row.get('p2_approvals', 0)}",
    ]
    return "\n\n".join(["## Server log facts (phase 2 window)", "\n".join(facts), "## Screens after the change", *screens])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("out")
    ap.add_argument("--seed", type=int, required=True)
    a = ap.parse_args()
    dirs = sorted(p.parent for p in Path(a.results).glob("*/row.json"))
    dirs = [d for d in dirs if code_q3.code(d).get("p2_outcome") not in ("invalid-setup", "invalid-external")]
    ids = [f"P{i:03d}" for i in range(1, len(dirs) + 1)]
    random.Random(a.seed).shuffle(ids)
    out = Path(a.out)
    (out / "packets").mkdir(parents=True, exist_ok=True)
    key, sheet = [], []
    for pid, d in sorted(zip(ids, dirs)):
        (out / "packets" / f"{pid}.md").write_text(f"# Packet {pid}\n\n{INSTRUCTIONS}\n\n{packet(d)}\n")
        key.append({"id": pid, "repetition": d.name})
        sheet.append({"id": pid, "outcome": "", "note": ""})
    for name, rows in [("key.csv", key), ("coding-sheet.csv", sheet)]:
        with open(out / name, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print(json.dumps({"packets": len(key), "out": str(out)}))

if __name__ == "__main__":
    main()
