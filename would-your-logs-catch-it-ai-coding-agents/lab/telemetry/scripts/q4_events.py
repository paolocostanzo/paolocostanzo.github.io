#!/usr/bin/env python3
"""Host-side orchestration of the Q4 event runs (D24 point 3), with the client-log export of D24 points 1-2.

Three event types on the six terminal clients, two runs per type and client (36 runs), in a random order fixed by --seed:
  q5-start      a server declared by the repository starts after the folder is trusted (trusting-a-repo, run_q5_matrix.py)
  q3-announced  an approved tool's definition changes and the server sends notifications/tools/list_changed (run_q3_cell.py)
  q3-silent     the same change without the notification
plan.json is written before the first run and is what every later invocation executes: a restart with a different seed,
snapshot, client list, inventory, candidates file or harness (git commit of the lab, clean tree) is refused.
For every run: restore the clean snapshot, start the VM, copy tool-change/, trusting-a-repo/ and telemetry/ in, run the cell,
stop every client (tmux), then BEFORE the next restore:
  - archive the client's default log locations (vm_logs.py collect, window = the run's start and end) and check the archive
    (collection_check.py); a failed client-log export is recorded and the run is excluded with its reason (D24 point 2);
  - copy the run's outputs and the ground-truth log (the lab server's log for Q3 events, the canary's log for Q5) and verify
    that they are present and readable. If they are not, the attempt is kept under <run>/attempts/<n>/, nothing is marked
    complete, and the campaign stops with the VM left as it is: without ground truth, "no event" cannot be told from a lost one.
  - scan everything copied out for secrets (secret_gate.py, D26): fresh fingerprints of the VM's secrets, never their values,
    then exact and format matching over the run folder, archives included. Token-shaped data inside the raw client-log archive
    (private evidence, D24) is counted in secret-marks.json and does not stop the run; a lab secret found by exact value,
    token-shaped data anywhere else, an unreadable secret store or a file that cannot be scanned keeps the attempt as
    "secret-found" and stops the campaign (exit 9): that attempt is never published.
Whether the event happened is decided later from the ground-truth logs only, never from the client's logs.

  python3 q4_events.py --snapshot SNAPSHOT --seed SEED --inventory INVENTORY.json --out OUT
The list of log locations is always the repository's telemetry/inventory/candidates.json: it is what the VM receives with the
harness, and the plan freezes it through the harness commit and its own SHA-256.
  python3 q4_events.py ... --dry-run          # prints the plan and the commands, touches nothing
"""
import argparse, collections, csv, datetime as dt, fcntl, hashlib, importlib.util, io, json, os, random, shutil, subprocess, sys, tarfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB = HERE.parent.parent  # repository root: tool-change/, trusting-a-repo/, telemetry/
FOLDERS = ["tool-change", "trusting-a-repo", "telemetry"]
CLIENTS = ["claude-code", "codex", "gemini", "qwen", "cursor-agent", "goose"]
EVENTS = ["q5-start", "q3-announced", "q3-silent"]
# Q5 cell in which the client trusts the folder (and approves the server where it asks); Goose has no trust step and never
# starts a repository-declared server (part 2: 0/2), so its runs are expected to be "no event" (D24 point 2).
Q5_CELL = {"claude-code": "after-decision", "codex": "after-decision", "gemini": "after-decision", "qwen": "after-decision",
           "cursor-agent": "after-decision", "goose": "open-in-repo"}
Q3_SIGNAL = {"q3-announced": "list_changed", "q3-silent": "none"}
CANDIDATES = LAB / "telemetry/inventory/candidates.json"
# Host telemetry copied after every run and session: name -> (command in the VM, must be non-empty)
TELEMETRY = [("audit.log", "sync; sleep 1; sudo cat /var/log/audit/audit.log", True),
             ("journal.txt", "sudo journalctl -o short-iso-precise --since @{since} --no-pager", True),
             ("nft-ruleset.txt", "sudo nft list ruleset", False),
             ("auditctl-rules.txt", "sudo auditctl -l", False)]
MIN_AVAIL_MB, MIN_ORDER6, MEM_WAIT_S = 1024, 20, 900  # plan of 17 Oct 2026, phase 0.4
C_LOCALE = {**os.environ, "LC_ALL": "C"}  # virsh states in English whatever the host locale ("shut off", not "spento")
STOP_CLIENTS = "Q4_IN_VM=1 bash ~/lab/telemetry/scripts/stop_clients.sh"  # exits non-zero if a client, daemon or lab server survives


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sg = load(HERE / "secret_gate.py", "secret_gate")  # D26: no secret may reach the copied evidence
PRIVATE_ARCHIVES = {"client-logs.tgz"}  # D24 point 1: raw client logs stay in the private evidence


def plan(seed, clients=CLIENTS, events=EVENTS, reps=2):
    """Random order within each repetition block, as in the Q3 matrix: every (client, event) once per block."""
    rng = random.Random(seed)
    runs = []
    for rep in range(1, reps + 1):
        block = [(c, e) for c in clients for e in events]
        rng.shuffle(block)
        runs += [{"rep": rep, "client": c, "event": e, "name": f"{c}__{e}__r{rep}"} for c, e in block]
    return runs


QUOTA_WATCH = "~/q4-out/quota-watch.jsonl"
BOUNDS = "~/q4-out/command-bounds.json"


def watched(command, client, session):
    """Run command with the quota observer reading the client's tmux pane every second (D25 point 5); keeps command's exit code."""
    # The observer must be watching before the command starts; the wrapper records the command's own start and end, and the
    # observer's exit status, in q4-out/command-bounds.json, so that the host can check the observer covered the whole command.
    return (f"python3 ~/lab/telemetry/scripts/quota_watch.py --client {client} --session {session} --out {QUOTA_WATCH} & W=$!; "
            f"for i in $(seq 1 100); do grep -q '\"started\"' {QUOTA_WATCH} 2>/dev/null && break; sleep 0.1; done; "
            f"S=$(date +%s.%N); ( {command} ); rc=$?; E=$(date +%s.%N); kill -TERM $W 2>/dev/null; wait $W; WRC=$?; "
            f"printf '{{\"start\": %s, \"end\": %s, \"rc\": %s, \"observer_rc\": %s}}\\n' $S $E $rc $WRC > {BOUNDS}; exit $rc")


MAX_GAP_S = 30  # the observer writes at least every 10 s (heartbeat); more than this between two records means it stalled


def observer_problems(text, bounds_text=None):
    """Problems with the quota observer's record of a run or session (D25 point 5); empty list = it watched the whole command and
    saw no quota message. bounds_text is q4-out/command-bounds.json, written by the wrapper: the observer must have started
    before the command started, stopped in order after it ended, and exited 0. Quota messages are reported here too."""
    try:
        recs = [json.loads(l) for l in text.splitlines() if l.strip()]
    except ValueError:
        return ["quota observer: record unreadable"]
    kinds = [r.get("event") for r in recs]
    if not recs or kinds[0] != "started":
        return ["quota observer: did not start"]
    problems = []
    if "failed" in kinds:
        f = next(r for r in recs if r.get("event") == "failed")
        problems.append(f"quota observer: failed ({f.get('error')})")
    if "stopped" not in kinds:
        problems.append("quota observer: no orderly end (it died or was killed before the run ended)")
    try:
        b = json.loads(bounds_text) if bounds_text else None
    except ValueError:
        b = None
    if not b:
        problems.append("quota observer: no command bounds (the wrapper did not finish)")
    else:
        stop = next((r for r in recs if r.get("event") == "stopped"), None)
        if recs[0].get("ts", 0) > b.get("start", 0):
            problems.append("quota observer: started after the command")
        if stop is None or stop.get("ts", 0) < b.get("end", 0):
            problems.append("quota observer: stopped before the command ended")
        if b.get("observer_rc") != 0:
            problems.append(f"quota observer: exit status {b.get('observer_rc')}")
    ts = [r.get("ts", 0) for r in recs]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    if gaps and max(gaps) > MAX_GAP_S:
        problems.append(f"quota observer: a gap of {max(gaps):.0f} s between records (it stalled)")
    errors = [r for r in recs if r.get("event") == "capture-error"]
    if errors:
        problems.append(f"quota observer: {len(errors)} capture error(s), e.g. {errors[0].get('error')!r}")
    seen = [r for r in recs if r.get("event") == "quota"]
    if seen:
        problems.append(f"quota or rate limit reached (observer): {seen[0].get('message')!r} (stop: Paolo's rule)")
    return problems


def cell_command(run):
    """The command run inside the VM for one run (outputs under ~/q4-out), with the quota observer next to it."""
    c, e, rep = run["client"], run["event"], run["rep"]
    if e == "q5-start":
        return watched(f"python3 ~/lab/trusting-a-repo/scripts/run_q5_matrix.py --lab ~/lab/trusting-a-repo --out ~/q4-out "
                       f"--reps 1 --clients {c} --only {c}:{Q5_CELL[c]}", c, "q5run")
    return watched(f"python3 ~/lab/tool-change/scripts/run_q3_cell.py --client {c} --signal {Q3_SIGNAL[e]} --change description "
                   f"--rep {rep} --out ~/q4-out", c, "q3run")


def collect_command(t0, t1):
    return f"python3 -I ~/lab/telemetry/scripts/vm_logs.py collect ~/lab/telemetry/inventory/candidates.json {t0} {t1}"


def ground_truth_command(run):
    """Required files only, so that tar fails if one is missing (no --ignore-failed-read)."""
    gt = "canary-start.log" if run["event"] == "q5-start" else "q3-server.log"
    return f"cd ~ && tar -czf - q4-out {gt}"


def parse_jsonl(text, required):
    """Records of a JSON Lines log; problems for every line that is not a JSON object with the required fields and a valid ts."""
    recs, problems = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            problems.append(f"line {n} is not JSON")
            continue
        if not isinstance(r, dict) or any(k not in r for k in required):
            problems.append(f"line {n} lacks {[k for k in required if not isinstance(r, dict) or k not in r]}")
            continue
        try:
            dt.datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00"))
        except ValueError:
            problems.append(f"line {n} has no valid ts")
            continue
        recs.append(r)
    return recs, problems


def validate_ground_truth(run, tgz_bytes):
    """Problems with the copied outputs and ground truth of one run (empty list = usable). Does not decide the event."""
    try:
        tar = tarfile.open(fileobj=io.BytesIO(tgz_bytes), mode="r:gz")
        names = set(tar.getnames())
    except (tarfile.TarError, OSError, EOFError) as exc:
        return [f"outputs archive unreadable: {exc}"]
    problems = []
    if run["event"] == "q5-start":
        if "canary-start.log" not in names:
            problems.append("canary-start.log missing")
        else:  # empty is a valid "no event"; anything written must be well formed
            _, bad = parse_jsonl(tar.extractfile("canary-start.log").read().decode("utf-8", "replace"), ("ts", "event", "client"))
            problems += [f"canary-start.log: {b}" for b in bad]
        mats = [n for n in names if n.startswith("q4-out/") and n.endswith("/matrix.csv")]
        if len(mats) != 1:
            problems.append(f"expected one matrix.csv, found {len(mats)}")
        else:
            rows = list(csv.DictReader(io.TextIOWrapper(tar.extractfile(mats[0]), encoding="utf-8")))
            if [(r["client"], r["cell"]) for r in rows] != [(run["client"], Q5_CELL[run["client"]])]:
                problems.append(f"matrix.csv does not hold exactly this cell: {[(r['client'], r['cell']) for r in rows]}")
    else:
        cell = f"q4-out/{run['client']}__{Q3_SIGNAL[run['event']]}__description__r{run['rep']}/row.json"
        cell_id = f"{run['client']}:{Q3_SIGNAL[run['event']]}:description:r{run['rep']}"  # as run_q3_cell.py names it
        if cell not in names:
            problems.append(f"{cell} missing")
        else:
            try:
                row = json.load(tar.extractfile(cell))
                if (row.get("client"), row.get("signal"), row.get("rep")) != (run["client"], Q3_SIGNAL[run["event"]], run["rep"]):
                    problems.append(f"row.json belongs to another cell: {row.get('client')}/{row.get('signal')}/r{row.get('rep')}")
            except (ValueError, AttributeError) as exc:
                problems.append(f"row.json unreadable: {exc}")
        if "q3-server.log" not in names:
            problems.append("q3-server.log missing")
        else:
            recs, bad = parse_jsonl(tar.extractfile("q3-server.log").read().decode("utf-8", "replace"), ("ts", "event", "cell"))
            problems += [f"q3-server.log: {b}" for b in bad]
            changes = [r for r in recs if r["event"] == "definition-changed" and r["cell"] == cell_id and r.get("to") == "B"]
            if not recs:
                problems.append("q3-server.log empty")
            elif len(changes) != 1:
                problems.append(f"q3-server.log: expected one A->B change for {cell_id}, found {len(changes)}")
    return problems


def quota_in_outputs(client, tgz_bytes, quota_mod):
    """The first quota or rate-limit message seen in the run: by the observer (q4-out/quota-watch.jsonl, D25 point 5), in the
    saved screen captures, or None. A missing observer record is itself reported, so "nothing seen" is never assumed."""
    try:
        tar = tarfile.open(fileobj=io.BytesIO(tgz_bytes), mode="r:gz")
    except (tarfile.TarError, OSError, EOFError):
        return None
    names = set(tar.getnames())
    if "q4-out/quota-watch.jsonl" not in names:
        return "the quota observer left no record (it did not run)"
    bounds = tar.extractfile("q4-out/command-bounds.json").read().decode() if "q4-out/command-bounds.json" in names else None
    obs = observer_problems(tar.extractfile("q4-out/quota-watch.jsonl").read().decode("utf-8", "replace"), bounds)
    if obs:
        return "; ".join(obs)
    for m in tar.getmembers():
        if m.isfile() and m.name.startswith("q4-out/") and m.name.endswith(".txt"):
            msg = quota_mod.find(client, tar.extractfile(m).read().decode("utf-8", "replace"))
            if msg:
                return f"{m.name}: {msg}"
    return None


def wait_for_memory(note, sleep=time.sleep, now=time.time, meminfo="/proc/meminfo", buddyinfo="/proc/buddyinfo",
                    min_mb=MIN_AVAIL_MB, min_order6=MIN_ORDER6, wait_s=MEM_WAIT_S):
    """Before a restore: at least min_mb available and min_order6 free blocks of order >= 6 in zone Normal (libvirt needs one to
    load the VM's AppArmor profile). Waits up to wait_s, then stops the campaign cleanly (exit 4)."""
    t0 = now()
    while True:
        avail = int(next(l for l in open(meminfo) if l.startswith("MemAvailable")).split()[1]) // 1024
        order6 = next((sum(int(x) for x in l.split()[10:]) for l in open(buddyinfo) if " Normal " in l), 0)
        if avail >= min_mb and order6 >= min_order6:
            return avail, order6
        if now() - t0 > wait_s:
            note(event="abort-memory", avail_mb=avail, order6plus=order6, need_mb=min_mb, need_order6=min_order6,
                 hint="close heavy applications on the host (or compact memory), then rerun the same command")
            sys.exit(4)
        sleep(30)


def safe(fn, *args, **kw):
    """Run an ssh or virsh call; a timeout or transport error becomes a failed result (rc 124 / 255) with what was received,
    instead of an exception that would leave the run without its attempt record."""
    try:
        return fn(*args, **kw)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return _Result(124, out, (err + f"\ntimeout after {exc.timeout} s").strip())
    except OSError as exc:
        return _Result(255, "", f"{type(exc).__name__}: {exc}")


class _Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def fresh_vm_checked(h, q3, snapshot, note, sleep=time.sleep):
    """Restore the clean snapshot like q3_host.Host.fresh_vm (which stays as it ran for part 3), but with the memory check of
    the plan before EVERY revert attempt, and with timeouts turned into failures.
    A running VM is powered off first (Codex review of D27, round 1, R2): with the VM running the host cannot reach the memory the
    check asks for, so every restore after the first would wait and stop. This is done only when no attempt is pending anywhere:
    every caller recovers or keeps the previous attempt (all copies out of the VM done) before restoring."""
    if (STATE / "pending.json").exists():
        note(event="abort-vm-holds-evidence", hint="a pending attempt is recorded: rerun its campaign, it recovers before restoring")
        sys.exit(7)
    state = safe(q3.run, q3.VIRSH + ["domstate", q3.DOMAIN], env=C_LOCALE).stdout.strip()
    if state != "shut off":  # running, paused, pmsuspended, in shutdown...: all hold the VM's memory
        r = safe(q3.run, q3.VIRSH + ["destroy", q3.DOMAIN], env=C_LOCALE)
        after = safe(q3.run, q3.VIRSH + ["domstate", q3.DOMAIN], env=C_LOCALE).stdout.strip()
        note(event="vm-powered-off-before-restore", state=state, rc=r.returncode, state_after=after)
        if after != "shut off":  # a destroy that failed because the VM had just stopped is fine
            note(event="abort-power-off", stderr=(r.stderr or "").strip()[:300], hint="power the VM off (virsh destroy), then rerun")
            sys.exit(4)
    for attempt in range(1, 6):
        wait_for_memory(note)
        r = safe(q3.run, q3.VIRSH + ["snapshot-revert", q3.DOMAIN, snapshot], env=C_LOCALE)
        state = safe(q3.run, q3.VIRSH + ["domstate", q3.DOMAIN], env=C_LOCALE).stdout.strip()
        if r.returncode == 0 and state != "running":  # a shut-off snapshot: start it (Codex review of D27, round 3, R5)
            r = safe(q3.run, q3.VIRSH + ["start", q3.DOMAIN], env=C_LOCALE)
        if r.returncode == 0:
            break
        note(event="revert-failed", attempt=attempt, stderr=(r.stderr or "").strip()[:300], order6plus=h.order6_blocks())
        sleep(60)
    else:
        note(event="abort-revert", hint="host memory fragmented or VM not restorable: free memory or reboot, then rerun")
        sys.exit(4)
    t0 = time.time()
    while time.time() - t0 < 240:
        ip = h.ip()
        if ip and safe(h.ssh, ip, "true", timeout=15).returncode == 0:
            # the guest clock comes back with the snapshot: set it, then measure the skew; runs need one clock
            set_ = safe(h.ssh, ip, f"sudo date -s @{int(time.time())} >/dev/null", timeout=30)
            skew = clock_skew(h, ip)
            if set_.returncode != 0 or skew is None or abs(skew) > MAX_SKEW_S:
                note(event="abort-clock", set_rc=set_.returncode, skew_s=skew, hint="guest clock not in step with the host")
                sys.exit(6)
            return ip, round(time.time() - t0), skew
        sleep(3)
        sleep(3)
    note(event="ssh-timeout")
    sys.exit(6)


MAX_SKEW_S = 2


def clock_skew(h, ip):
    """Guest minus host time in seconds (guest read between two host readings), or None if it cannot be read."""
    before = time.time()
    r = safe(h.ssh, ip, "date +%s.%N", timeout=15)
    after = time.time()
    try:
        return round(float(r.stdout.strip()) - (before + after) / 2, 3) if r.returncode == 0 else None
    except ValueError:
        return None


STATE = Path.home() / ".agentlab-q4"  # VM-wide state, shared by every campaign and output folder on this host


def write_json_atomic(path, obj):
    """Completion and state records are written to a temporary file and renamed, so an interruption never leaves a truncated one."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, path)


def vm_lock(note):
    """One campaign at a time drives the VM: an exclusive lock held for the life of the process (released on exit)."""
    STATE.mkdir(exist_ok=True)
    fh = open(STATE / "vm.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        note(event="abort-locked", hint="another Q4 campaign is driving the VM")
        sys.exit(8)
    return fh


def state_fingerprints():
    """The host's fingerprint state (D26), or [] when there is none yet or it is malformed (format matching still applies)."""
    fp = STATE / "secret-fingerprints.json"
    try:
        d = json.loads(fp.read_text())
    except (OSError, ValueError):
        return []
    return [] if sg.fingerprint_problems(d, require_kinds=()) else d["fingerprints"]


def secret_problems(h, ip, d):
    """D26: take fresh fingerprints of the VM's secrets (never their values) into the host state, then scan everything copied out
    for this run or session, archives included, by exact value and by format. Returns problems that name only files and kinds;
    a store that cannot be read or a file that cannot be scanned is a problem too (the gate fails closed)."""
    r = safe(h.ssh, ip, "cd ~/lab && python3 telemetry/scripts/secret_gate.py fingerprints", timeout=60)
    try:
        new = json.loads(r.stdout)
    except ValueError:
        return [f"secret gate: no fingerprints from the VM (exit {r.returncode})"]
    bad = sg.fingerprint_problems(new) or ([f"fingerprints exit {r.returncode}"] if r.returncode != 0 else [])
    if bad:  # a store that vanished, cannot be read, or an entry that can never match would leave a secret unchecked
        return ["secret gate: fingerprints from the VM not usable: " + "; ".join(bad)]
    fp = STATE / "secret-fingerprints.json"  # host-only state, outside the run folders: never published
    try:
        old = json.loads(fp.read_text()) if fp.exists() else None
    except ValueError:
        return ["secret gate: the host's fingerprint state is not readable"]
    if old is not None and sg.fingerprint_problems(old, require_kinds=()):
        return ["secret gate: the host's fingerprint state is malformed: " + "; ".join(sg.fingerprint_problems(old, ()))]
    merged = sg.merge_fingerprints(old, new)
    write_json_atomic(fp, merged)
    os.chmod(fp, 0o600)
    findings, failures = sg.scan_paths([d], merged["fingerprints"], skip={"attempts"})
    # Paolo's rule of 17 Oct 2026: inside the raw client-log archive, which D24 keeps in the private evidence, data that only has
    # the shape of a token (for instance a client's own short-lived session tokens in its default logs) is counted and marked,
    # never published, and used only through a redacted copy; it does not stop the run. A lab secret found by exact value,
    # token-shaped data anywhere else, or anything that cannot be scanned stops the campaign.
    marked, in_stream = collections.Counter(), collections.Counter()
    stop = []
    for f in findings:
        if os.path.relpath(f["path"], d) in PRIVATE_ARCHIVES and not sg.is_exact(f):
            # the flat scan of a whole tar stream finds member contents a second time: counted apart
            (in_stream if (f["member"] or "").endswith(sg.TAR_STREAM) else marked)[sg.base_kind(f["kind"])] += 1
        else:
            stop.append(f)
    marks = {"private_archives": sorted(PRIVATE_ARCHIVES), "token_shaped_by_kind": dict(sorted(marked.items())),
             "token_shaped_in_whole_tar_streams_by_kind": dict(sorted(in_stream.items())),
             "rule": "private evidence (D24): never published; every later reader uses the redacted copy (D26)"}
    findings = stop
    raw = d / "client-logs.tgz"
    if not findings and not failures and raw.exists():
        # the redacted derivative every later reader uses, with its provenance; checked clean before it is written
        try:
            data = raw.read_bytes()
            body, n = sg.redact_blob(raw.name, data, merged["fingerprints"])
            left, bad = sg.scan_blob(raw.name, body, merged["fingerprints"])
        except (OSError, sg.Unscannable) as exc:
            return [f"secret gate: no redacted copy of client-logs.tgz ({sg.redact(str(exc), merged['fingerprints'])})"]
        if left or bad:
            return ["secret gate: the redacted copy of client-logs.tgz is not clean"]
        (d / "client-logs.redacted.tgz").write_bytes(body)
        marks["redacted_copy"] = {"file": "client-logs.redacted.tgz", "spans_replaced": n,
                                  "raw_sha256": hashlib.sha256(data).hexdigest(),
                                  "redacted_sha256": hashlib.sha256(body).hexdigest()}
    if raw.exists():
        write_json_atomic(d / "secret-marks.json", marks)
    problems = []
    if findings:
        where = sorted({(os.path.relpath(f["path"], d), f["member"] or "", f["kind"]) for f in findings})
        problems.append(f"secret gate: {len(findings)} finding(s): " + "; ".join(f"{p} {m} {k}".replace("  ", " ") for p, m, k in where))
    if failures:
        problems.append("secret gate: could not scan " + "; ".join(f"{os.path.relpath(f['path'], d)} ({f['error']})"
                                                                   for f in failures))
    return problems


def export_problems(check):
    """A run or session whose client-log export failed is not usable: it is kept as an attempt and retried (D27 point 10, which
    replaces the exclusion of D24 point 2), never recorded as complete. Only the failure category is quoted (paths can name the lab accounts' services)."""
    return [] if check.get("export") == "ok" else [f"client-log export failed: {(check.get('reason') or '').split(':')[0]}"]


def mark_pending(d, **info):
    """Written before any remote work of a run or session, in the run folder and in the VM-wide record; cleared only when the
    run is complete or its attempt is kept after every copy out of the VM succeeded."""
    rec = {"at": now(), "folder": str(d.resolve()), **info}
    write_json_atomic(d / "pending.json", rec)
    STATE.mkdir(exist_ok=True)
    write_json_atomic(STATE / "pending.json", rec)


def clear_pending(d):
    (d / "pending.json").unlink(missing_ok=True)
    g = STATE / "pending.json"
    if g.exists() and json.loads(g.read_text()).get("folder") == str(d.resolve()):
        g.unlink()


def guard_vm(d, note):
    """Before restoring for folder d: the VM must not hold the evidence of an attempt recorded for another folder."""
    g = STATE / "pending.json"
    if not g.exists():
        return
    rec = json.loads(g.read_text())
    other = Path(rec.get("folder", ""))
    if other == d.resolve():
        if not (d / "pending.json").exists():  # keep_attempt stopped after keeping the attempt: the record is stale
            note(event="stale-vm-pending-cleared", folder=str(other))
            g.unlink()
        return  # otherwise this folder's own attempt: recover_pending() handles it
    if (other / "pending.json").exists():
        note(event="abort-vm-holds-other-evidence", folder=str(other),
             hint="the VM holds an interrupted attempt of another campaign: rerun that campaign first, it recovers before restoring")
        sys.exit(7)
    note(event="stale-vm-pending-cleared", folder=str(other))  # that folder already kept its attempt
    g.unlink()


def completed(d):
    """True when d/run.json is a complete record; a malformed one is set aside so that the run goes through recovery."""
    f = d / "run.json"
    if not f.exists():
        return False
    try:
        rec = json.loads(f.read_text())
        if isinstance(rec, dict) and "t1" in rec:
            return True
    except ValueError:
        pass
    os.replace(f, d / f"run.json.malformed-{int(time.time())}")
    return False


def recover_pending(d, h, rn, cc, inv, cand_sha, client, gt_command, note):
    """A run or session interrupted while the VM held its evidence: before any restore, copy what the VM still has (client logs,
    outputs and ground truth, host telemetry) into the folder and keep it as an attempt. If the VM cannot be reached, stop:
    restoring now would destroy the evidence. Returns the kept folder, or None when nothing was pending."""
    pf = d / "pending.json"
    if not pf.exists():
        return None
    info = json.loads(pf.read_text())
    ip = h.ip()
    if not ip or safe(h.ssh, ip, "true", timeout=15).returncode != 0:
        note(event="abort-pending-unreachable", folder=str(d),
             hint="an interrupted attempt may still have its evidence in the VM, which does not answer: do not restore; "
                  "look at the VM (virsh list), and delete pending.json only after deciding what to keep")
        sys.exit(7)
    t0, t1 = int(info.get("t0") or 0), int(time.time())
    stop = safe(h.ssh, ip, STOP_CLIENTS)
    if stop.returncode != 0:
        note(event="abort-pending-not-quiescent", folder=str(d), hint="a writer survived in the VM; do not restore; look at it")
        sys.exit(7)
    rc_logs = rn.copy_client_logs(ip, d, t0, t1)
    rc_out, err_out = rn.ssh_bin(ip, gt_command, d / "vm-out.tgz")
    (d / "copy.stderr.txt").write_text(f"outputs (exit {rc_out}):\n{err_out}\n")
    problems, copied = rn.telemetry(ip, d, t0 - 5)
    if rc_logs != 0 or rc_out != 0 or not copied:
        # pending.json stays: the next invocation tries again; nothing is restored until every copy has succeeded
        note(event="abort-pending-copy-failed", folder=str(d), collect_rc=rc_logs, outputs_rc=rc_out,
             telemetry_copied=copied, hint="evidence could not be copied out of the VM; do not restore")
        sys.exit(7)
    leaks = secret_problems(h, ip, d)  # D26: before anything reads what was copied
    export = None
    if not leaks:
        rec = rn.check_client_logs(d, client, t0, t1, cc, inv, cand_sha, rc_logs)
        export, leaks = rec["export"], rec.get("secret_problems") or []
    kept = keep_attempt(d, "secret-found" if leaks else "interrupted-recovered",
                        {"pending": info, "export": export, "outputs_rc": rc_out,
                         "telemetry_problems": [] if leaks else problems, "secret_problems": leaks})
    if leaks:
        note(event="abort-secret", folder=str(d), problems=leaks, kept=str(kept),
             hint="a secret reached the copied evidence: never publish this attempt; tell the author and rotate the secret")
        sys.exit(9)
    return kept


def preserve_interrupted(d):
    """A run folder with content but no run.json is an attempt that was interrupted (crash, power, Ctrl-C): keep it before the
    VM is restored again, so that a rerun never overwrites it."""
    if d.exists() and not (d / "run.json").exists() and any(p.name != "attempts" for p in d.iterdir()):
        return keep_attempt(d, "interrupted", {"note": "found without run.json when the campaign was resumed"})
    return None


def sha_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def harness_identity():
    """The lab's git commit and the tree of every folder pushed into the VM; refuses a dirty tree."""
    dirty = subprocess.run(["git", "-C", str(LAB), "status", "--porcelain", "--", *FOLDERS], capture_output=True, text=True).stdout
    if dirty.strip():
        raise SystemExit("the harness folders have uncommitted changes: commit them first, the plan records the commit\n" + dirty)
    head = subprocess.run(["git", "-C", str(LAB), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    trees = {f: subprocess.run(["git", "-C", str(LAB), "rev-parse", f"HEAD:{f}"], capture_output=True, text=True,
                               check=True).stdout.strip() for f in FOLDERS}
    return {"commit": head, "trees": trees}


def load_or_create_plan(out, frozen, runs):
    """frozen: the identity of this campaign (arguments and input hashes). Returns the runs to execute."""
    pf = out / "plan.json"
    if pf.exists():
        old = json.loads(pf.read_text())
        diff = {k: (old.get("frozen", {}).get(k), v) for k, v in frozen.items() if old.get("frozen", {}).get(k) != v}
        if diff:
            raise SystemExit(f"plan.json was made with other inputs; refusing to continue: {json.dumps(diff)[:800]}")
        return old["runs"]
    write_json_atomic(pf, {"created": now(), "frozen": frozen, "runs": runs})
    return runs


def keep_attempt(d, status, extra):
    """Move whatever a failed attempt left into d/attempts/<n>/ with a status file; the run stays not complete. Called only
    when nothing is left in the VM that was not copied, so it also clears the VM-wide pending record for d."""
    att = d / "attempts"
    att.mkdir(exist_ok=True)
    n = len([p for p in att.iterdir() if p.is_dir()]) + 1
    dest = att / str(n)
    dest.mkdir()
    for p in list(d.iterdir()):
        if p.name != "attempts":
            shutil.move(str(p), str(dest / p.name))
    (dest / "attempt.json").write_text(json.dumps({"status": status, "at": now(), **extra}, indent=1))
    g = STATE / "pending.json"
    if g.exists() and json.loads(g.read_text()).get("folder") == str(d.resolve()):
        g.unlink()
    return dest


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Runner:
    def __init__(self, host, q3):
        self.h, self.q3 = host, q3  # q3_host.Host instance and the q3_host module (VM constants, ssh key)

    def ssh_bin(self, ip, command, dest, timeout=300):
        """Run a command in the VM and write its stdout, as bytes, to dest."""
        q3 = self.q3
        with open(dest, "wb") as f:
            try:
                r = subprocess.run(["ssh", "-i", str(q3.KEY), "-o", f"UserKnownHostsFile={q3.KNOWN}", "-o", "BatchMode=yes",
                                    f"lab@{ip}", command], stdout=f, stderr=subprocess.PIPE, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                return 124, f"timeout after {exc.timeout} s"
            except OSError as exc:
                return 255, f"{type(exc).__name__}: {exc}"
        return r.returncode, r.stderr.decode(errors="replace")[-500:]

    def push(self, ip):
        q3 = self.q3
        p1 = subprocess.Popen(["tar", "-C", str(LAB), "-czf", "-", "--exclude=node_modules", "--exclude=__pycache__", *FOLDERS],
                              stdout=subprocess.PIPE)
        p2 = subprocess.Popen(["ssh", "-i", str(q3.KEY), "-o", f"UserKnownHostsFile={q3.KNOWN}", "-o", "BatchMode=yes", f"lab@{ip}",
                               "mkdir -p ~/lab && cd ~/lab && rm -rf " + " ".join(FOLDERS) + " && tar -xzf -"],
                              stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p1.stdout.close()  # only ssh reads the pipe: if ssh dies, tar gets SIGPIPE instead of blocking
        try:
            p2.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            p2.kill(); p2.communicate()
        try:
            p1.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p1.kill(); p1.wait()
        rc = p2.returncode if p2.returncode != 0 else p1.returncode
        h = safe(self.h.ssh, ip, "cd ~/lab && find " + " ".join(FOLDERS) + " -type f -not -path '*/test/*' | sort | xargs sha256sum",
                 timeout=120)
        return (rc if h.returncode == 0 else (rc or h.returncode)), h.stdout

    def telemetry(self, ip, d, since):
        """Copy the host telemetry. Returns (problems, copied): problems is empty when complete; copied is False when a copy
        itself failed (the VM may still hold what was not copied). Every source keeps its stderr and exit code."""
        tel = d / "telemetry"
        tel.mkdir(exist_ok=True)
        problems, status = [], {}
        for name, rcmd, nonempty in TELEMETRY:
            r = safe(self.h.ssh, ip, rcmd.format(since=since), timeout=120)
            (tel / name).write_text(r.stdout)
            (tel / (name + ".stderr")).write_text(r.stderr or "")
            status[name] = {"rc": r.returncode, "bytes": len(r.stdout.encode())}
            if r.returncode != 0:
                problems.append(f"telemetry {name}: exit {r.returncode}: {(r.stderr or '').strip()[:200]}")
            elif nonempty and (not r.stdout.strip() or r.stdout.strip() == "-- No entries --"):
                problems.append(f"telemetry {name}: empty")
        (tel / "status.json").write_text(json.dumps(status, indent=1))
        return problems, all(s["rc"] == 0 for s in status.values())

    def copy_client_logs(self, ip, d, t0, t1):
        """Copy the client-log archive out of the VM, and nothing else: it is read only after the secret gate (D26)."""
        rc, err = self.ssh_bin(ip, collect_command(t0, t1), d / "client-logs.tgz")
        (d / "collect.stderr.txt").write_text(f"collect (exit {rc}):\n{err}\n")
        return rc

    def check_client_logs(self, d, client, t0, t1, cc, inv, cand_sha, rc):
        """After the secret gate: check the archive and write the collection records. The integrity check reads the raw archive
        (it compares file hashes with the manifest) as part of the gate's own trusted handling (D26); what it writes is redacted
        first and scanned after, and a finding is returned in check["secret_problems"]. A check that breaks on a malformed
        archive is a failed export, not a crash."""
        fps = state_fingerprints()
        try:
            check = cc.check(str(d / "client-logs.tgz"), client, t0, t1, inv, cand_sha)
        except Exception as exc:
            check = {"client": client, "start": t0, "end": t1, "export": "failed",
                     "reason": f"collection check failed: {type(exc).__name__}"}
        check = sg.redact_obj(check, fps)
        check["collect_rc"] = rc
        if rc != 0 and check["export"] == "ok":
            check["export"], check["reason"] = "failed", f"collect exited {rc}"
        (d / "collection.json").write_text(json.dumps(check, indent=1))  # private: names client files
        (d / "collection-public.json").write_text(json.dumps(cc.public_record(check), indent=1))
        found, bad = sg.scan_paths([d / "collection.json", d / "collection-public.json"], fps)
        if found or bad:
            check["secret_problems"] = [f"secret gate: collection records not clean ({len(found)} finding(s), {len(bad)} unscanned)"]
        return check


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--inventory", required=True, help="inventory.json taken on the clean snapshot (D24 point 1)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--clients", default=",".join(CLIENTS))
    ap.add_argument("--reps", type=int, default=2, help="repetitions per client and event (D24: 2; 1 for the pipeline check)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    runs = plan(a.seed, a.clients.split(","), reps=a.reps)
    if a.dry_run:
        for i, r in enumerate(runs, 1):
            print(f"{i:2} {r['name']:32} {cell_command(r)}")
        print("then:", collect_command("<t0>", "<t1>"))
        return
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    frozen = {"snapshot": a.snapshot, "seed": a.seed, "clients": a.clients.split(","), "reps": a.reps, "q5_cell": Q5_CELL,
              "inventory_sha256": sha_file(a.inventory), "candidates_sha256": sha_file(CANDIDATES), "harness": harness_identity()}
    lock = vm_lock(lambda **k: print(json.dumps(k), flush=True))  # noqa: F841 before any campaign state is read or written
    runs = load_or_create_plan(out, frozen, runs)
    q3 = load(LAB / "tool-change/scripts/q3_host.py", "q3_host")
    cc = load(HERE / "collection_check.py", "collection_check")
    qm = load(HERE / "quota.py", "quota")
    inv = json.load(open(a.inventory))
    h = q3.Host(out)
    rn = Runner(h, q3)
    for i, r in enumerate(runs, 1):
        d = out / r["name"]
        if completed(d):
            if (d / "pending.json").exists():  # interrupted between run.json and its cleanup: run.json comes after every check
                clear_pending(d)
                h.note(event="pending-cleared-for-completed-run", run=r["name"])
            continue
        d.mkdir(exist_ok=True)
        guard_vm(d, h.note)
        kept = recover_pending(d, h, rn, cc, inv, frozen["candidates_sha256"], r["client"], ground_truth_command(r), h.note) \
            or preserve_interrupted(d)
        if kept:
            h.note(event="interrupted-attempt-kept", run=r["name"], kept=str(kept))
        d.mkdir(exist_ok=True)
        h.note(event="pre-revert", run=r["name"], order6plus=h.order6_blocks())
        ip, boot_s, skew = fresh_vm_checked(h, q3, a.snapshot, h.note)
        rc_push, hashes = rn.push(ip)
        (d / "harness-sha256.txt").write_text(hashes)
        if rc_push != 0:
            dest = keep_attempt(d, "harness-not-copied", {"push_rc": rc_push})
            h.note(event="abort-push", run=r["name"], kept=str(dest), hint="the harness did not reach the VM; nothing was run")
            sys.exit(5)
        safe(h.ssh, ip, "rm -rf ~/q4-out && mkdir -p ~/q4-out")
        t0 = int(time.time())
        mark_pending(d, run=r["name"], t0=t0, ip=ip)
        h.note(event="run-start", n=i, of=len(runs), run=r["name"], ip=ip, boot_s=boot_s, push_rc=rc_push)
        res = safe(h.ssh, ip, cell_command(r), timeout=1500)
        t1 = int(time.time())
        (d / "cell.stdout.txt").write_text(res.stdout + "\n--- stderr ---\n" + res.stderr)
        stop = safe(h.ssh, ip, STOP_CLIENTS)  # databases and logs are closed before they are copied
        if stop.returncode != 0:  # nothing copied yet: pending.json stays, so a rerun recovers before any restore
            (d / "stop.stdout.txt").write_text(stop.stdout[-4000:] + "\n--- stderr ---\n" + (stop.stderr or ""))
            h.note(event="abort-not-quiescent", run=r["name"], hint="a writer survived; VM left as it is, evidence still in it")
            sys.exit(5)
        # D26: every copy first; the campaign log gets only exit codes until the secret gate has passed
        rc_logs = rn.copy_client_logs(ip, d, t0, t1)
        rc_gt, err_gt = rn.ssh_bin(ip, ground_truth_command(r), d / "vm-out.tgz")
        (d / "copy.stderr.txt").write_text(f"ground truth (exit {rc_gt}):\n{err_gt}\n")
        tel_problems, copied = rn.telemetry(ip, d, t0 - 5)
        if rc_logs != 0 or rc_gt != 0 or not copied:  # a copy failed: the VM may hold the rest
            h.note(event="abort-copy-failed", run=r["name"], collect_rc=rc_logs, ground_truth_rc=rc_gt,
                   telemetry_copied=copied,
                   hint="evidence not fully copied out of the VM; pending.json stays; rerun to retry the copy before any restore")
            sys.exit(5)
        leaks = secret_problems(h, ip, d)
        if leaks:  # D26: before anything reads the evidence or quotes it in the campaign log
            dest = keep_attempt(d, "secret-found", {"problems": leaks, "t0": t0, "t1": t1})
            h.note(event="abort-secret", run=r["name"], problems=leaks, kept=str(dest),
                   hint="a secret reached the copied evidence: never publish this attempt; tell the author and rotate the secret")
            sys.exit(9)
        check = rn.check_client_logs(d, r["client"], t0, t1, cc, inv, frozen["candidates_sha256"], rc_logs)
        if check.get("secret_problems"):  # D26: a record written from the private archive is not clean
            dest = keep_attempt(d, "secret-found", {"problems": check["secret_problems"], "t0": t0, "t1": t1})
            h.note(event="abort-secret", run=r["name"], problems=check["secret_problems"], kept=str(dest),
                   hint="a secret reached a collection record: never publish this attempt; tell the author")
            sys.exit(9)
        problems = validate_ground_truth(r, (d / "vm-out.tgz").read_bytes()) + tel_problems
        problems = export_problems(check) + problems  # D27 point 10: retried until it counts (Codex review, round 1, R1)
        if res.returncode != 0:
            problems.insert(0, f"cell exited {res.returncode}: {(res.stderr or '').strip()[-300:]}")
        q = quota_in_outputs(r["client"], (d / "vm-out.tgz").read_bytes(), qm) or qm.find(r["client"], res.stdout)
        if q:
            problems.append(f"quota or rate limit reached: {q} (stop: Paolo's rule)")
        if problems:  # everything was copied, but the run is not usable: keep it as an attempt and stop
            dest = keep_attempt(d, "not-usable", {"problems": problems, "t0": t0, "t1": t1, "cell_rc": res.returncode})
            h.note(event="abort-run", run=r["name"], problems=problems, kept=str(dest),
                   hint="VM left as it is for inspection: look at it before rerunning, a rerun powers it off; then rerun the same command (the plan is kept)")
            sys.exit(5)
        write_json_atomic(d / "run.json", {**r, "t0": t0, "t1": t1, "clock_skew_s": skew, "cell_rc": res.returncode,
                                            "push_rc": rc_push, "export": check["export"], "export_reason": check.get("reason"),
                                            "client_logs": check.get("client_logs"), "ground_truth": "copied and checked"})
        clear_pending(d)
        h.note(event="run-end", run=r["name"], rc=res.returncode, seconds=t1 - t0, export=check["export"],
               client_logs=check.get("client_logs"))
    h.note(event="events-done", runs=len(runs))
    q3.run(q3.VIRSH + ["shutdown", q3.DOMAIN])


if __name__ == "__main__":
    main()
