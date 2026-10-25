#!/usr/bin/env python3
"""Host-side orchestration of the Q4 baseline (D13 points 1-3, D24 point 4): scripted normal use per terminal client.

Plan: one development session per client, in a random order fixed by --seed, then one holdout session per client in the same
order. plan.json is written before the first session and is what every later invocation executes; a restart with other
arguments or inputs (snapshot, seed, minutes, gap, clients, parts, inventory, candidates, harness commit) is refused.
A holdout session starts only when at least --gap-hours (default 6) have passed since the end of that client's VALID
development session; the runner waits otherwise. Every session starts from the clean snapshot and gets the same export as the
event runs (q4_events.py). A session counts only if its session.json says valid (definition A only, no change, the normal use
happened: baseline/tasks.json "workload_success"), its client logs were exported (collection check ok), and the outputs and the
lab server's log were copied; otherwise the attempt
is kept under <session>/attempts/<n>/ and the campaign stops, so that a person looks at it before more quota is spent.
The local-engine repetition (D13 point 1) is not implemented and is reported as not done.

  python3 q4_baseline.py --snapshot q3-base-20261015 --seed SEED --inventory INVENTORY.json --out OUT [--minutes 60] [--dry-run]
  python3 q4_baseline.py ... --parts setup --minutes 6      # exploratory set-up sessions (not part of the baseline)
"""
import argparse, datetime as dt, importlib.util, io, json, random, sys, tarfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("q4_events", HERE / "q4_events.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)
PARTS = ("setup", "dev", "holdout")


def plan(seed, clients=ev.CLIENTS, parts=("dev", "holdout")):
    bad = [p for p in parts if p not in PARTS]
    if bad:
        raise SystemExit(f"unknown part(s) {bad}: only {PARTS} exist (the local-engine repetition is not implemented)")
    order = list(clients)
    random.Random(seed).shuffle(order)
    return [{"client": c, "part": p, "name": f"baseline__{c}__{p}"} for p in parts for c in order]


def session_command(s, minutes):
    return ev.watched(f"python3 ~/lab/telemetry/scripts/baseline_session.py --client {s['client']} --part {s['part']} "
                      f"--minutes {minutes} --out ~/q4-out", s["client"], "q3run")


def outputs_command():
    return "cd ~ && tar -czf - q4-out q3-server.log"  # required files only: tar fails if one is missing


def check_outputs(s, tgz_bytes):
    """(session.json or None, problems)."""
    try:
        tar = tarfile.open(fileobj=io.BytesIO(tgz_bytes), mode="r:gz")
        names = set(tar.getnames())
    except (tarfile.TarError, OSError, EOFError) as exc:
        return None, [f"outputs archive unreadable: {exc}"]
    base = f"q4-out/{s['name']}"
    problems = [f"{base}/{f} missing" for f in ("session.json", "timeline.jsonl") if f"{base}/{f}" not in names]
    if "q3-server.log" not in names:
        problems.append("q3-server.log missing")
    if problems:
        return None, problems
    if "q4-out/quota-watch.jsonl" not in names:
        problems.append("quota observer: left no record (it did not run)")
    else:
        bounds = tar.extractfile("q4-out/command-bounds.json").read().decode() if "q4-out/command-bounds.json" in names else None
        problems += ev.observer_problems(tar.extractfile("q4-out/quota-watch.jsonl").read().decode("utf-8", "replace"), bounds)
    try:
        sess = json.load(tar.extractfile(f"{base}/session.json"))
    except ValueError as exc:
        return None, [f"session.json unreadable: {exc}"]
    if sess.get("quota_exhausted"):
        problems.append(f"quota or rate limit reached: {sess.get('quota_message')!r} (stop: Paolo's rule)")
    if not sess.get("valid"):
        problems.append(f"session not valid: definitions {sess.get('definitions_served')}, changed {sess.get('definition_changed')}, "
                        f"workload {sess.get('workload')}")
    return sess, problems


def setup_can_continue(part, problems):
    """An exploratory set-up session (D25 point 2) whose only problem is that the scripted use did not work is recorded and the
    set-up goes on to the next client; any other problem (quota, copy, export, telemetry, exit code) stops it, as in the baseline."""
    return part == "setup" and bool(problems) and all(p.startswith("session not valid") for p in problems)


PROVIDER_WAIT_S, PROVIDER_MAX = 3600, 3  # D28 point 6: at least one hour between provider-unavailable attempts, at most three


def attempt_session(f, rec):
    """The session summary of a kept attempt and whether a quota or rate limit was seen in it: from attempt.json, or, for an
    attempt recovered after an interruption, from the session.json and the quota observer's record inside its copied outputs,
    read with the same checks as a normal session (Codex review of D28, rounds 2-3, G2-03 and G3-01)."""
    if rec.get("session"):
        quota = any(str(p).startswith("quota or rate limit reached") for p in rec.get("problems") or [])
        return rec["session"], quota or bool(rec["session"].get("quota_exhausted"))
    tgz = f.parent / "vm-out.tgz"
    if rec.get("status") != "interrupted-recovered" or not tgz.exists():
        return {}, False
    try:
        tar = tarfile.open(tgz, mode="r:gz")
        names = set(tar.getnames())
        m = next((n for n in names if n.startswith("q4-out/") and n.endswith("/session.json")), None)
        sess = json.load(tar.extractfile(m)) if m else {}
        quota = bool(sess.get("quota_exhausted"))
        if "q4-out/quota-watch.jsonl" in names:
            bounds = tar.extractfile("q4-out/command-bounds.json").read().decode() if "q4-out/command-bounds.json" in names else None
            seen = ev.observer_problems(tar.extractfile("q4-out/quota-watch.jsonl").read().decode("utf-8", "replace"), bounds)
            quota = quota or any(p.startswith("quota or rate limit reached") for p in seen)
        return sess, quota
    except (tarfile.TarError, OSError, EOFError, ValueError):
        return {}, False


def provider_attempts(d):
    """Times of the kept attempts of session folder d that failed the workload check with at least one error reply from the
    provider (D28 point 7: "provider unavailable"), oldest first. A quota or rate-limit stop is never one: it keeps the reset-
    and-resume rule of D27 point 8 (Codex review of D28, round 2, G2-02)."""
    times = []
    for f in sorted((d / "attempts").glob("*/attempt.json")) if (d / "attempts").is_dir() else []:
        rec = json.loads(f.read_text())
        if rec.get("status") not in ("session-not-counted", "interrupted-recovered"):
            continue
        sess, quota = attempt_session(f, rec)
        if (sess.get("error_replies") or 0) >= 1 and not (sess.get("workload") or {}).get("ok", False) and not quota:
            times.append(dt.datetime.fromisoformat(rec["at"]).timestamp())
    return sorted(times)


def incomplete_clients(out):
    f = out / "incomplete-clients.json"
    return json.loads(f.read_text()) if f.exists() else {}


def provider_gate(out, s, note, sleep=time.sleep, now=time.time):
    """Before a development or holdout session (D28 point 6). After a provider-unavailable attempt, wait until one hour has
    passed since it. After three, the client's baseline is incomplete: it is recorded in incomplete-clients.json (written by
    this runner, never by hand) and the client's remaining sessions are skipped. Returns True when the session is skipped."""
    if s["part"] == "setup":
        return False
    times = provider_attempts(out / s["name"])
    if len(times) >= PROVIDER_MAX:
        inc = incomplete_clients(out)
        inc.setdefault(s["client"], {"session": s["name"], "provider_attempts": len(times), "at": ev.now(),
                                     "reason": "provider unavailable in three attempts (D28 point 6)"})
        ev.write_json_atomic(out / "incomplete-clients.json", inc)
        note(event="client-incomplete", session=s["name"], provider_attempts=len(times))
        return True
    if times:
        wait = times[-1] + PROVIDER_WAIT_S - now()
        if wait > 0:
            note(event="provider-wait", session=s["name"], wait_s=round(wait), provider_attempts=len(times))
            sleep(wait)
    return False


def wait_for_gap(out, s, gap_hours, note, sleep=time.sleep):
    """Holdout sessions start at least gap_hours after the end of the same client's valid development session."""
    if s["part"] != "holdout":
        return 0
    dd = out / f"baseline__{s['client']}__dev"
    if not ev.completed(dd):
        raise SystemExit(f"{s['name']}: no valid development session for {s['client']} yet")
    dev = json.loads((dd / "run.json").read_text())
    wait = dev["t1"] + gap_hours * 3600 - time.time()
    if wait > 0:
        note(event="holdout-wait", session=s["name"], wait_s=round(wait))
        sleep(wait)
    return max(0, round(wait))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--gap-hours", type=float, default=6)
    ap.add_argument("--clients", default=",".join(ev.CLIENTS))
    ap.add_argument("--parts", default="dev,holdout")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sessions = plan(a.seed, a.clients.split(","), a.parts.split(","))
    if a.dry_run:
        for i, s in enumerate(sessions, 1):
            print(f"{i:2} {s['name']:34} {session_command(s, a.minutes)}")
        return
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    frozen = {"snapshot": a.snapshot, "seed": a.seed, "minutes": a.minutes, "gap_hours": a.gap_hours,
              "clients": a.clients.split(","), "parts": a.parts.split(","), "inventory_sha256": ev.sha_file(a.inventory),
              "candidates_sha256": ev.sha_file(ev.CANDIDATES), "harness": ev.harness_identity()}
    lock = ev.vm_lock(lambda **k: print(json.dumps(k), flush=True))  # noqa: F841 before any campaign state is read or written
    sessions = ev.load_or_create_plan(out, frozen, sessions)
    q3 = ev.load(ev.LAB / "tool-change/scripts/q3_host.py", "q3_host")
    cc = ev.load(HERE / "collection_check.py", "collection_check")
    inv = json.load(open(a.inventory))
    h = q3.Host(out)
    rn = ev.Runner(h, q3)
    for i, s in enumerate(sessions, 1):
        d = out / s["name"]
        if ev.completed(d):
            if (d / "pending.json").exists():  # interrupted between run.json and its cleanup
                ev.clear_pending(d)
                h.note(event="pending-cleared-for-completed-session", session=s["name"])
            continue
        if s["client"] in incomplete_clients(out):  # D28 point 6: no more sessions for a client whose baseline is incomplete
            h.note(event="session-skipped-client-incomplete", session=s["name"])
            continue
        d.mkdir(exist_ok=True)
        ev.guard_vm(d, h.note)
        kept = ev.recover_pending(d, h, rn, cc, inv, frozen["candidates_sha256"], s["client"], outputs_command(), h.note) \
            or ev.preserve_interrupted(d)
        if kept:
            h.note(event="interrupted-attempt-kept", session=s["name"], kept=str(kept))
        if provider_gate(out, s, h.note):
            continue
        waited = wait_for_gap(out, s, a.gap_hours, h.note)
        d.mkdir(exist_ok=True)
        ip, boot_s, skew = ev.fresh_vm_checked(h, q3, a.snapshot, h.note)
        rc_push, hashes = rn.push(ip)
        (d / "harness-sha256.txt").write_text(hashes)
        if rc_push != 0:
            dest = ev.keep_attempt(d, "harness-not-copied", {"push_rc": rc_push})
            h.note(event="abort-push", session=s["name"], kept=str(dest), hint="the harness did not reach the VM; nothing was run")
            sys.exit(5)
        ev.safe(h.ssh, ip, "rm -rf ~/q4-out && mkdir -p ~/q4-out")
        t0 = int(time.time())
        ev.mark_pending(d, session=s["name"], t0=t0, ip=ip)
        h.note(event="session-start", n=i, of=len(sessions), session=s["name"], ip=ip, boot_s=boot_s)
        res = ev.safe(h.ssh, ip, session_command(s, a.minutes), timeout=int(a.minutes * 60) + 900)
        t1 = int(time.time())
        (d / "session.stdout.txt").write_text(res.stdout + "\n--- stderr ---\n" + res.stderr)
        stop = ev.safe(h.ssh, ip, ev.STOP_CLIENTS)
        if stop.returncode != 0:  # nothing copied yet: pending.json stays, so a rerun recovers before any restore
            (d / "stop.stdout.txt").write_text(stop.stdout[-4000:] + "\n--- stderr ---\n" + (stop.stderr or ""))
            h.note(event="abort-not-quiescent", session=s["name"], hint="a writer survived; VM left as it is, evidence still in it")
            sys.exit(5)
        # D26: every copy first; the campaign log gets only exit codes until the secret gate has passed
        rc_logs = rn.copy_client_logs(ip, d, t0, t1)
        rc_out, err_out = rn.ssh_bin(ip, outputs_command(), d / "vm-out.tgz")
        (d / "copy.stderr.txt").write_text(f"outputs (exit {rc_out}):\n{err_out}\n")
        tel_problems, copied = rn.telemetry(ip, d, t0 - 5)
        if rc_logs != 0 or rc_out != 0 or not copied:  # a copy failed: the VM may hold the rest
            h.note(event="abort-copy-failed", session=s["name"], collect_rc=rc_logs, outputs_rc=rc_out,
                   telemetry_copied=copied,
                   hint="evidence not fully copied out of the VM; pending.json stays; rerun to retry the copy before any restore")
            sys.exit(5)
        leaks = ev.secret_problems(h, ip, d)
        if leaks:  # D26: before anything reads the evidence, set-up sessions included
            dest = ev.keep_attempt(d, "secret-found", {"problems": leaks, "t0": t0, "t1": t1})
            h.note(event="abort-secret", session=s["name"], problems=leaks, kept=str(dest),
                   hint="a secret reached the copied evidence: never publish this attempt; tell the author and rotate the secret")
            sys.exit(9)
        check = rn.check_client_logs(d, s["client"], t0, t1, cc, inv, frozen["candidates_sha256"], rc_logs)
        if check.get("secret_problems"):  # D26: a record written from the private archive is not clean
            dest = ev.keep_attempt(d, "secret-found", {"problems": check["secret_problems"], "t0": t0, "t1": t1})
            h.note(event="abort-secret", session=s["name"], problems=check["secret_problems"], kept=str(dest),
                   hint="a secret reached a collection record: never publish this attempt; tell the author")
            sys.exit(9)
        sess, problems = check_outputs(s, (d / "vm-out.tgz").read_bytes())
        if res.returncode != 0:
            problems.insert(0, f"session exited {res.returncode}")
        problems = ev.export_problems(check) + problems  # a session without its logs is of no use (D24 point 4)
        problems += tel_problems
        if setup_can_continue(s["part"], problems):
            # exploratory set-up session (D25 point 2): a workload failure is what the session is for; record it and go on to
            # the next client. Quota, copy, export and safety problems still stop.
            ev.write_json_atomic(d / "run.json", {**s, "t0": t0, "t1": t1, "clock_skew_s": skew, "session_rc": res.returncode,
                                                   "export": check["export"], "valid": False, "problems": problems,
                                                   "workload": (sess or {}).get("workload")})
            ev.clear_pending(d)
            h.note(event="setup-session-recorded", session=s["name"], problems=problems)
            continue
        if problems:  # everything was copied, but the session does not count: keep it as an attempt and stop
            dest = ev.keep_attempt(d, "session-not-counted", {"problems": problems, "t0": t0, "t1": t1, "session": sess})
            if (sess or {}).get("error_replies"):
                h.note(event="provider-unavailable", session=s["name"], error_replies=sess["error_replies"],
                       hint="D28 point 6: rerun the same command; the runner waits one hour and stops the client after three")
            h.note(event="abort-session", session=s["name"], problems=problems, kept=str(dest),
                   hint="VM left as it is: look at it and at the attempt before rerunning, a rerun powers it off; fix the cause, then rerun the same command (the plan is kept)")
            sys.exit(5)
        ev.write_json_atomic(d / "run.json", {**s, "t0": t0, "t1": t1, "clock_skew_s": skew, "waited_s": waited,
                                               "session_rc": res.returncode, "push_rc": rc_push, "export": check["export"],
                                               "export_reason": check.get("reason"), "client_logs": check.get("client_logs"),
                                               "workload": sess["workload"]})
        ev.clear_pending(d)
        h.note(event="session-end", session=s["name"], seconds=t1 - t0, export=check["export"], workload_ok=sess["workload"]["ok"])
    h.note(event="baseline-done", sessions=len(sessions), local_engine_repetition="not implemented, not done")
    q3.run(q3.VIRSH + ["shutdown", q3.DOMAIN])


if __name__ == "__main__":
    main()
