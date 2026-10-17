#!/usr/bin/env python3
"""Runs one Q3 repetition ("does a client notice when an approved tool's definition changes?")
for one terminal client, unattended, inside a disposable VM restored from a clean snapshot.

Phases of one repetition (deviations D12 and D16 of the preregistration):
  1. open the client in the (already trusted) test folder, ask it to call the lab tool, give the
     client's persistent per-tool approval at the first call, wait until the server logs the call;
  2. switch the tool definition A -> B (and, at the list_changed level, the server sends the
     notification), wait, ask for the same call again, record whatever the client shows;
  3. (exploratory) close the client, reopen it, ask for the call once more.
Every screen is captured as text; the server log (every message in and out, with the definition
served by each tools/list) is the record that the coder uses.

Only for a throwaway VM: it edits the clients' MCP configuration and approvals in $HOME.

  python3 run_q3_cell.py --client codex --signal list_changed --rep 1 --out ~/q3-out
"""
import argparse, datetime as dt, json, os, re, shutil, subprocess, time
from pathlib import Path

HOME = Path.home()
LAB = Path(__file__).resolve().parent.parent          # .../tool-change
SERVER = LAB / "server" / "q3-server.mjs"
FLIP = LAB / "scripts" / "flip.mjs"
REPO = HOME / "q3-repo"
CONTROL = HOME / "q3-control.json"
LOG = HOME / "q3-server.log"
SESSION = "q3run"
SECRETS = ". ~/.agentlab_secrets 2>/dev/null; unset GOOGLE_API_KEY;"
PROMPT = "Call the q3_noop tool of the q3-lab MCP server once, then reply with only the word it returned."

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")

def sh(cmd, check=False, timeout=120):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check, timeout=timeout)

# ---------------------------------------------------------------- server log
def events():
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"event": "unparseable-log-line", "raw": line[:300]})
    return out

def calls_since(t0):
    return [e for e in events() if e.get("event") == "tools/call" and e.get("ts", "") >= t0]

# ---------------------------------------------------------------- tmux
def kill():
    sh(f"tmux kill-session -t {SESSION}")
    sh("pkill -f q3-server.mjs")
    time.sleep(2)

def launch(cmd):
    kill()
    sh(f"tmux new-session -d -s {SESSION} -x 180 -y 50 -c {REPO} \"bash -lc '{cmd}; sleep 900'\"", check=True)

def screen():
    return sh(f"tmux capture-pane -p -J -t {SESSION}").stdout

def keys(*ks):
    for k in ks:
        sh(f"tmux send-keys -t {SESSION} {k}")
        time.sleep(0.8)

def type_text(text):
    # literal text, then Enter as a separate key so that multi-line editors do not swallow it
    sh(f"tmux send-keys -t {SESSION} -l {json.dumps(text)}")
    time.sleep(0.6)
    sh(f"tmux send-keys -t {SESSION} Enter")

# A client's own permission classifier was unavailable and it fell back to asking (Qwen Code, 15 Oct 2026, D19).
CLASSIFIER_UNAVAILABLE = r"couldn't classify this action|[Cc]lassifier stage \d+ unavailable|classifier (?:is )?unavailable"

# First-run screens unrelated to the tool change, answered with their non-default "no" (as in Q5).
NOISE = [
    (r"Yes, try it", ("Down", "Enter")),
    (r"Share anonymous usage data", ("Right", "Enter")),
    (r"Cannot use the background server", ("Up", "Enter")),
]

def answer_noise(s):
    for tip, answer in NOISE:
        if re.search(tip, s):
            keys(*answer)
            return True
    return False

# ---------------------------------------------------------------- clients
# approve: regex of the client's permission question for an MCP tool call, and the keys that give the
# persistent per-tool approval ("always allow this tool" or nearest equivalent, D16 point 3).
# notice: regex of any message that tells the user the server's tools changed.
# These are filled from the set-up runs (exploratory, D16 point 8) and frozen before the primary runs.
CLIENTS = {
    "claude-code": {"cmd": "claude", "version": "claude --version", "ready": r"│\s*>|❯|\? for shortcuts",
                    "approve": None, "approve_keys": None, "persistent": None, "notice": None, "quit": ("C-c", "C-c")},
    "codex":       {"cmd": "codex", "version": "codex --version", "ready": r"›|To get started|send",
                    "approve": None, "approve_keys": None, "persistent": None, "notice": None, "quit": ("C-c", "C-c")},
    "gemini":      {"cmd": f"{SECRETS} gemini", "version": "gemini --version", "ready": r"Type your message",
                    "approve": None, "approve_keys": None, "persistent": None, "notice": None, "quit": ("C-c", "C-c")},
    "qwen":        {"cmd": f"{SECRETS} qwen --auth-type gemini -m gemini-flash-latest", "version": "qwen --version",
                    "ready": r"Type your message", "approve": None, "approve_keys": None, "persistent": None, "notice": None,
                    "quit": ("C-c", "C-c")},
    "cursor-agent": {"cmd": "cursor-agent", "version": "cursor-agent --version", "ready": r"→|Plan, search, build|Add a follow-up",
                    "approve": None, "approve_keys": None, "persistent": None, "notice": None, "quit": ("C-c", "C-c")},
    "goose":       {"cmd": ". ~/.agentlab_secrets 2>/dev/null; unset GEMINI_API_KEY; export GOOSE_DISABLE_KEYRING=1; goose session",
                    "version": "goose --version", "ready": r"goose is ready|\( O\)>|>\s*$",
                    "approve": None, "approve_keys": None, "persistent": None, "notice": None, "quit": ("C-c", "C-c")},
}

def load_overrides(path):
    """Per-client patterns decided in the set-up runs live in a JSON file next to this script.
    A client's "noise" list adds (regex, keys) pairs for unrelated screens (answered with their "no")."""
    p = Path(path)
    if p.exists():
        for name, spec in json.loads(p.read_text()).items():
            if name in CLIENTS:
                for tip, answer in spec.pop("noise", None) or []:
                    NOISE.append((tip, tuple(answer)))
                CLIENTS[name].update({k: (tuple(v) if isinstance(v, list) else v) for k, v in spec.items()})

def wait_ready(spec, timeout, caps, tag, outdir, row):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = screen()
        if answer_noise(s):
            continue
        if re.search(spec["ready"], s):
            return True
        time.sleep(1.5)
    capture(caps, outdir, row, f"{tag}-not-ready")
    return False

def capture(caps, outdir, row, label):
    name = f"{row['client']}__{row['signal']}__r{row['rep']}__{len(caps):02d}-{label}.txt"
    (outdir / name).write_text(screen())
    caps.append(name)
    return name

def ask_and_follow(spec, phase, t_after, timeout, caps, outdir, row):
    """Sends the fixed prompt; answers the tool-permission question with the persistent approval;
    returns when the server logs a call after t_after, or on timeout."""
    type_text(PROMPT)
    row[f"{phase}_prompt_at"] = now()
    approvals, notices = [], []
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = screen()
        answer_noise(s)
        if spec.get("notice") and re.search(spec["notice"], s) and not notices:
            notices.append(capture(caps, outdir, row, f"{phase}-notice"))
        if re.search(CLASSIFIER_UNAVAILABLE, s):
            row[f"{phase}_classifier_unavailable"] = True
        if spec.get("approve") and re.search(spec["approve"], s):
            approvals.append(capture(caps, outdir, row, f"{phase}-approval"))
            if spec.get("approve_keys"):
                keys(*spec["approve_keys"])
            else:
                # a question the client does not normally ask (D19): record it and accept the pre-selected answer
                row[f"{phase}_unexpected_approval"] = True
                keys("Enter")
            time.sleep(2)
            continue
        if calls_since(t_after):
            time.sleep(4)  # let the client render the result
            break
        time.sleep(1.5)
    row[f"{phase}_approvals"] = len(approvals)
    row[f"{phase}_notice_seen"] = bool(notices) or bool(row.get(f"{phase}_notice_seen"))
    row[f"{phase}_calls"] = len(calls_since(t_after))
    row[f"{phase}_timeout"] = not calls_since(t_after)
    row[f"{phase}_end_at"] = now()  # end of the phase window, before the runner touches the client
    capture(caps, outdir, row, f"{phase}-end")

def flip_init(signal, change, cell):
    sh(f"node {FLIP} --control {CONTROL} --log {LOG} --init --signal {signal} --change {change} --cell {cell}", check=True)

def flip_to_b():
    sh(f"node {FLIP} --control {CONTROL} --log {LOG} --to B", check=True)

def run(client, signal, change, rep, outroot, reopen=True, dwell_after_flip=12, call_timeout=150):
    spec = CLIENTS[client]
    cell = f"{client}:{signal}:{change}:r{rep}"
    outdir = Path(outroot) / f"{client}__{signal}__{change}__r{rep}"
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)
    row = {"client": client, "signal": signal, "change": change, "rep": rep, "cell": cell, "t_start": now(),
           "version_before": sh(spec["version"] + " 2>&1 | head -1").stdout.strip(), "prompt": PROMPT,
           "persistent_approval": spec.get("persistent")}
    caps = []
    LOG.write_text("")
    flip_init(signal, change, cell)
    # phase 1
    launch(spec["cmd"])
    row["ready1"] = wait_ready(spec, 90, caps, "p1", outdir, row)
    capture(caps, outdir, row, "p1-open")
    t1 = now()
    ask_and_follow(spec, "p1", t1, call_timeout, caps, outdir, row)
    # change
    time.sleep(4)
    row["flip_at"] = now()
    flip_to_b()
    time.sleep(dwell_after_flip)
    capture(caps, outdir, row, "p2-after-flip")
    # a change message can appear right after the change, before the second request (e.g. Gemini CLI)
    if spec.get("notice") and re.search(spec["notice"], screen()):
        row["p2_notice_seen"] = True
        capture(caps, outdir, row, "p2-notice")
    ask_and_follow(spec, "p2", row["flip_at"], call_timeout, caps, outdir, row)
    # phase 3 (exploratory)
    if reopen:
        keys(*spec["quit"])
        time.sleep(3)
        kill()
        row["reopen_at"] = now()
        launch(spec["cmd"])
        row["ready3"] = wait_ready(spec, 90, caps, "p3", outdir, row)
        capture(caps, outdir, row, "p3-open")
        ask_and_follow(spec, "p3", row["reopen_at"], call_timeout, caps, outdir, row)
    kill()
    row["t_end"] = now()
    row["version_after"] = sh(spec["version"] + " 2>&1 | head -1").stdout.strip()
    row["captures"] = caps
    shutil.copy2(LOG, outdir / "q3-server.log")
    shutil.copy2(CONTROL, outdir / "q3-control.json")
    (outdir / "row.json").write_text(json.dumps(row, indent=2))
    return outdir, row

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True, choices=list(CLIENTS))
    ap.add_argument("--signal", required=True, choices=["none", "list_changed"])
    ap.add_argument("--change", default="description", choices=["description", "schema", "name"])
    ap.add_argument("--rep", type=int, required=True)
    ap.add_argument("--out", default=str(HOME / "q3-out"))
    ap.add_argument("--clients-json", default=str(Path(__file__).resolve().parent / "q3-clients.json"))
    ap.add_argument("--no-reopen", action="store_true")
    a = ap.parse_args()
    load_overrides(a.clients_json)
    outdir, row = run(a.client, a.signal, a.change, a.rep, a.out, reopen=not a.no_reopen)
    print(json.dumps({k: row[k] for k in row if k != "captures"}))
    print(f"done: {outdir}")

if __name__ == "__main__":
    main()
