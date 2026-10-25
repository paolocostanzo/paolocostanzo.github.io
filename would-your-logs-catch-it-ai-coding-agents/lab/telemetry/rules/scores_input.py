#!/usr/bin/env python3
"""Builds score.py's input from the frozen rules and the recorded runs (D27 points 2–5, D31 points 2–3, D36).

For every rule:
  - event runs of its client and event type. The event time comes from the ground-truth logs only, never from a client's logs:
    for Q3, the lab server's one change of definition to B for the run's cell (as q4_events.py checks it); for Q5, the canary's
    "start" record of the run's client, moved back to the audit log's exec of the process whose pid that record names (D36: the
    exec precedes the record the process writes itself; the record decides whether the event happened, the exec only its time).
    A run without the event is "no-event". The run ends END_SLACK seconds after its t1, the moment the scripted session returned:
    clients are then stopped and logs copied (D24 point 5: "until the logs are copied"), within that bound.
  - holdout sessions: windows are cut from t_start to t_end of the session's session.json, on the VM clock (D27 point 3), with
    the coverage of D31 point 2 (windows holding at least one record of the rule's source).
Client-log rules come from the rule file and are applied with apply.py to each client's own runs and holdout; host rules come
from host.py, one per event type, over all clients' runs and holdouts, and are also given per client ("host/q5-start#codex").
Every planned run and holdout must be there and usable, and every planned (client, event) pair must have a rule: an absent or
unusable run or holdout is listed under "missing" with its client, and its client is incomplete (D27 point 4, D36); a planned
pair without a rule stops the build. A client listed in BASELINE_DIR/incomplete-clients.json (D28 point 6) is incomplete too.

  python3 scores_input.py RULES.json EVENTS_DIR BASELINE_DIR > SCORES_INPUT.json
  python3 scores_input.py --host KNOWN.json EVENTS_DIR BASELINE_DIR > SCORES_INPUT_HOST.json
EVENTS_DIR and BASELINE_DIR are the campaign folders (plan.json, one folder per completed run or session with run.json,
vm-out.tgz, client-logs.redacted.tgz, telemetry/audit.log). A client listed in BASELINE_DIR/incomplete-clients.json is reported
as incomplete (D28 point 6) for h5.py.
"""
import importlib.util, json, sys, tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("apply", HERE / "apply.py")
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)
_hspec = importlib.util.spec_from_file_location("host", HERE / "host.py")
host = importlib.util.module_from_spec(_hspec)
_hspec.loader.exec_module(host)
START_SLACK = 5   # s before t0: the clock skew measured at each run is well under a second
END_SLACK = 120   # s after t1 (D36): t1 is when the cell command returned; clients are then stopped and logs copied, within this
                  # bound, and the archive holds nothing written after its copy
Q3_SIGNAL = {"q3-announced": "list_changed", "q3-silent": "none"}  # as q4_events.py


def jsonl(text):
    return [json.loads(l) for l in text.splitlines() if l.strip()]


class Unusable(Exception):
    pass


def event_time(run_dir, run):
    """The event's time from the run's ground truth, or None (no event); Unusable when the ground truth cannot be parsed."""
    try:
        return _event_time(run_dir, run)
    except (ValueError, KeyError, TypeError, tarfile.TarError, OSError) as exc:
        raise Unusable(str(exc)) from exc


def _event_time(run_dir, run):
    with tarfile.open(run_dir / "vm-out.tgz") as t:
        if run["event"] == "q5-start":
            starts = [r for r in jsonl(t.extractfile("canary-start.log").read().decode("utf-8", "replace"))
                      if r.get("event") == "start" and r.get("client") == run["client"]]
            if not starts:
                return None
            te = ap.nz.to_epoch(starts[0]["ts"])
            audit = run_dir / "telemetry/audit.log"
            if starts[0].get("pid") and audit.is_file():
                te = host.exec_time_of(audit, int(starts[0]["pid"]), te) or te
            return te
        cell = f"{run['client']}:{Q3_SIGNAL[run['event']]}:description:r{run['rep']}"
        changes = [r for r in jsonl(t.extractfile("q3-server.log").read().decode("utf-8", "replace"))
                   if r.get("event") == "definition-changed" and r.get("cell") == cell and r.get("to") == "B"]
        return ap.nz.to_epoch(changes[0]["ts"]) if len(changes) == 1 else None


def readable(d, *names):
    """True when every named file of the folder exists and, for a .tgz, opens as an archive (FREEZE-02: evidence that is absent or
    unreadable at scoring time is listed as missing, not an exception)."""
    for n in names:
        f = d / n
        if not f.is_file():
            return False
        if n.endswith(".tgz"):
            try:
                with tarfile.open(f) as t:
                    t.getnames()
            except (tarfile.TarError, OSError, EOFError):
                return False
    return True


def client_archive_ok(folder, client):
    """The client-log evidence of a run or session is usable (Codex review of the freeze, FREEZE-02):
      - the runner's collection check (collection.json, D24 point 2, made on the original archive before redaction: manifest,
        explicit entry for the client, every frozen location accounted for, files and hashes, databases) says export "ok" for
        this client;
      - the redacted archive holds a manifest with an explicit entry for the client in "clients" and "patterns", and every file
        that entry lists. An entry listing no file (no default log) is a real observation and passes."""
    try:
        col = json.loads((folder / "collection.json").read_text())
        if col.get("export") != "ok" or col.get("client") != client:
            return False
        with tarfile.open(folder / "client-logs.redacted.tgz") as t:
            names = set(t.getnames())
            m = json.load(t.extractfile("manifest.json"))
        if not (isinstance(m, dict) and isinstance(m.get("clients"), dict) and isinstance(m.get("patterns"), dict)
                and client in m["clients"] and client in m["patterns"] and isinstance(m["clients"][client], list)):
            return False
        return all(isinstance(e, dict) and f"home/{e['path']}" in names for e in m["clients"][client])
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, EOFError):
        return False


def ground_truth_ok(run_dir, run):
    """vm-out.tgz holds the run's ground-truth log (FREEZE-02)."""
    member = "canary-start.log" if run["event"] == "q5-start" else "q3-server.log"
    try:
        with tarfile.open(run_dir / "vm-out.tgz") as t:
            return member in t.getnames()
    except (tarfile.TarError, OSError, EOFError):
        return False


def planned(folder):
    return json.loads((Path(folder) / "plan.json").read_text())["runs"]


def runs(events_dir, missing):
    for r in planned(events_dir):
        d = Path(events_dir) / r["name"]
        if not (d / "run.json").is_file():
            missing.append({"kind": "event run", "name": r["name"], "client": r["client"]})
            continue
        run = {**r, **json.loads((d / "run.json").read_text())}
        need = ("vm-out.tgz",) if run.get("export") != "ok" else ("vm-out.tgz", "client-logs.redacted.tgz")
        if not readable(d, *need) or not ground_truth_ok(d, run) or (
                run.get("export") == "ok" and not client_archive_ok(d, run["client"])):
            missing.append({"kind": "event run with absent or unreadable evidence", "name": r["name"], "client": r["client"]})
            continue
        yield d, run


def holdouts(baseline_dir, missing):
    for s in planned(baseline_dir):
        if s["part"] != "holdout":
            continue
        d = Path(baseline_dir) / s["name"]
        if not (d / "run.json").is_file():
            missing.append({"kind": "holdout", "name": s["name"], "client": s["client"]})
            continue
        try:
            window = host.session_window(d) if readable(d, "vm-out.tgz", "client-logs.redacted.tgz") else None
            if window is not None and not client_archive_ok(d, s["client"]):
                window = None
        except (StopIteration, KeyError, ValueError, tarfile.TarError, OSError):
            window = None
        if window is None:
            missing.append({"kind": "holdout with absent or unreadable evidence", "name": s["name"], "client": s["client"]})
            continue
        yield d, s["client"], window


def incomplete(baseline_dir, missing):
    p = Path(baseline_dir) / "incomplete-clients.json"
    runner = set(json.loads(p.read_text())) if p.is_file() else set()
    return sorted(runner | {m["client"] for m in missing})


def build(rules, events_dir, baseline_dir):
    missing, out = [], {rid: {"event_runs": [], "holdout": []} for rid in rules}
    pairs = {f"{r['client']}/{r['event']}" for r in planned(events_dir)}
    if pairs - set(rules):
        sys.exit(f"planned pairs without a rule: {sorted(pairs - set(rules))}")
    for d, run in runs(events_dir, missing):
        rid = f"{run['client']}/{run['event']}"
        if run.get("export") != "ok":
            out[rid]["event_runs"].append({"run": d.name, "status": "export-failed", "hits": []})
            missing.append({"kind": "unusable event run (export failed)", "name": d.name, "client": run["client"]})
            continue
        try:
            te = event_time(d, run)
        except Unusable:
            missing.append({"kind": "event run with unparsable ground truth", "name": d.name, "client": run["client"]})
            continue
        hits = ap.apply(rules, d / "client-logs.redacted.tgz", run["client"], run["t0"] - START_SLACK, run["t1"] + END_SLACK)
        out[rid]["event_runs"].append({"run": d.name, "event_time": te, "end": run["t1"] + END_SLACK,
                                       "status": "ok" if te is not None else "no-event", "hits": hits.get(rid, [])})
    for d, client, (t0, t1) in holdouts(baseline_dir, missing):
        archive = d / "client-logs.redacted.tgz"
        hits, cov = ap.apply(rules, archive, client, t0, t1), ap.coverage(rules, archive, client, t0, t1)
        for rid, times in hits.items():
            out[rid]["holdout"].append({"session": d.name, "t0": t0, "t1": t1, "hits": times, "covered_windows": cov.get(rid)})
    return {"rules": out, "missing": missing, "incomplete_clients": incomplete(baseline_dir, missing)}


def build_host(known, events_dir, baseline_dir):
    missing, out = [], {}
    clients = sorted({r["client"] for r in planned(events_dir)})
    ids = lambda rid, client: [rid, f"{rid}#{client}"]
    for rid in host.RULES:
        for c in [None] + clients:
            out[rid if c is None else f"{rid}#{c}"] = {"event_runs": [], "holdout": []}
    for d, run in runs(events_dir, missing):
        rid, audit = f"host/{run['event']}", d / "telemetry/audit.log"
        if run.get("export") != "ok" or not audit.is_file():
            entry = {"run": d.name, "status": "export-failed", "hits": []}
            missing.append({"kind": "unusable event run (no export or audit log)", "name": d.name, "client": run["client"]})
        else:
            try:
                te = event_time(d, run)
            except Unusable:
                missing.append({"kind": "event run with unparsable ground truth", "name": d.name, "client": run["client"]})
                continue
            hits = host.apply(known, audit, run["t0"] - START_SLACK, run["t1"] + END_SLACK)[rid]
            entry = {"run": d.name, "event_time": te, "end": run["t1"] + END_SLACK,
                     "status": "ok" if te is not None else "no-event", "hits": hits}
        for k in ids(rid, run["client"]):
            out[k]["event_runs"].append(entry)
    for d, client, (t0, t1) in holdouts(baseline_dir, missing):
        audit = d / "telemetry/audit.log"
        if not audit.is_file():
            missing.append({"kind": "holdout without audit log", "name": d.name, "client": client})
            continue
        cov = host.coverage(audit, t0, t1)
        for rid, times in host.apply(known, audit, t0, t1).items():
            for k in ids(rid, client):
                out[k]["holdout"].append({"session": d.name, "t0": t0, "t1": t1, "hits": times, "covered_windows": cov})
    return {"rules": out, "missing": missing, "incomplete_clients": incomplete(baseline_dir, missing)}


if __name__ == "__main__":
    if sys.argv[1] == "--host":
        print(json.dumps(build_host(json.load(open(sys.argv[2]))["known"], sys.argv[3], sys.argv[4]), indent=1))
    else:
        print(json.dumps(build(json.load(open(sys.argv[1]))["rules"], sys.argv[2], sys.argv[3]), indent=1))
