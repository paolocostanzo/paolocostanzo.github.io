#!/usr/bin/env python3
"""Runs the Q5 matrix ("does a client start a project-declared MCP server, and under
which trust decision?") on the terminal clients, unattended, inside a disposable VM.

For every client and cell it: resets that client's trust/approval state for the test
repository, regenerates the repository, empties the canary log, starts the client in
a tmux pane inside the repository, waits at the question, answers with a fixed key
sequence, and records the screen text and whether the canary started — before and
after the answer. Every cell is repeated --reps times.

Only for a throwaway VM: it edits the clients' trust stores in $HOME.

  python3 scripts/run_q5_matrix.py --canary ~/lab/canary/marker-server.mjs --out ~/q5-run
"""
import argparse, csv, datetime as dt, json, os, re, shutil, subprocess, time
from pathlib import Path

HOME = Path.home()
REPO = HOME / "q5-repo"
LOG = HOME / "canary-start.log"
SESSION = "q5run"
SECRETS = ". ~/.agentlab_secrets 2>/dev/null; unset GOOGLE_API_KEY;"

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

def sh(cmd, check=False, timeout=60):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check, timeout=timeout)

# ---------- canary + repo ----------
def starts():
    if not LOG.exists():
        return 0
    return sum(1 for l in LOG.read_text().splitlines() if '"event":"start"' in l)

def reset_log():
    LOG.write_text("")

def regenerate_repo(lab):
    if REPO.exists():
        shutil.rmtree(REPO)
    sh(f"node {lab}/scripts/make-test-repo.mjs --dir {REPO} --canary {lab}/canary/marker-server.mjs --log {LOG}", check=True)

# ---------- trust store resets (one per client) ----------
def _json_pop(path, key):
    p = Path(path)
    if not p.exists():
        return
    d = json.loads(p.read_text() or "{}")
    d.pop(key, None)
    p.write_text(json.dumps(d, indent=2))

def reset_claude():
    p = HOME / ".claude.json"
    d = json.loads(p.read_text())
    d.get("projects", {}).pop(str(REPO), None)
    p.write_text(json.dumps(d, indent=2))

def reset_gemini():
    _json_pop(HOME / ".gemini/trustedFolders.json", str(REPO))

def reset_codex():
    p = HOME / ".codex/config.toml"
    if not p.exists():
        return
    out, skip = [], False
    for line in p.read_text().splitlines():
        if line.strip() == f'[projects."{REPO}"]':
            skip = True
            continue
        if skip and line.strip().startswith("["):
            skip = False
        if not skip:
            out.append(line)
    p.write_text("\n".join(out).rstrip("\n") + "\n")

def reset_cursor():
    shutil.rmtree(HOME / ".cursor/projects" / str(REPO).strip("/").replace("/", "-"), ignore_errors=True)

def reset_qwen():
    _json_pop(HOME / ".qwen/mcpApprovals.json", str(REPO))

def reset_none():
    pass

# ---------- tmux ----------
def kill():
    sh(f"tmux kill-session -t {SESSION}")
    sh("pkill -f marker-server.mjs")
    time.sleep(2)

def launch(cmd):
    kill()
    sh(f"tmux new-session -d -s {SESSION} -x 160 -y 48 -c {REPO} \"bash -lc '{cmd}; sleep 900'\"", check=True)

def screen():
    return sh(f"tmux capture-pane -p -J -t {SESSION}").stdout

def keys(*ks):
    for k in ks:
        sh(f"tmux send-keys -t {SESSION} {k}")
        time.sleep(0.8)

def settle(seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        s = screen()
        for tip, answer in NOISE:
            if re.search(tip, s):
                keys(*answer)
        time.sleep(1.5)

def wait_for(pattern, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = screen()
        if re.search(pattern, s):
            return s
        for tip, answer in NOISE:
            if re.search(tip, s):
                keys(*answer)
        time.sleep(1.5)
    return None

# First-run screens unrelated to trust, answered with their non-default "no".
NOISE = [
    (r"Yes, try it", ("Down", "Enter")),                        # Claude Code feature tip
    (r"Share anonymous usage data", ("Right", "Enter")),         # Goose telemetry question
    # Codex: its background component updates itself and then refuses to start with the
    # installed CLI; "Cancel" is pre-selected. Choose "Run without daemon this time".
    (r"Cannot use the background server", ("Up", "Enter")),
]

CURSOR_MARKERS = ("❯", "›", "●", "▶")

def preselected(text):
    for line in (text or "").splitlines():
        s = line.strip().strip("│").strip()
        if s[:1] in CURSOR_MARKERS and len(s) > 2:
            return s
    return ""

# ---------- clients ----------
CLIENTS = {
    "claude-code": {
        "cmd": "claude", "version": "claude --version", "reset": reset_claude,
        "trust": r"Quick safety check|trust this folder", "mcp": r"New MCP server found",
    },
    "gemini": {
        "cmd": f"{SECRETS} gemini", "version": "gemini --version", "reset": reset_gemini,
        "trust": r"Do you trust the files in this folder",
    },
    "codex": {
        "cmd": "codex", "version": "codex --version", "reset": reset_codex,
        "trust": r"Trust this folder\?",
    },
    "cursor-agent": {
        "cmd": "cursor-agent", "version": "cursor-agent --version", "reset": reset_cursor,
        "trust": r"Workspace Trust Required", "mcp": r"MCP Server Approval Required",
    },
    "qwen": {
        "cmd": f"{SECRETS} qwen --auth-type gemini -m gemini-flash-latest", "version": "qwen --version",
        "reset": reset_qwen, "mcp": r"Untrusted MCP server",
    },
    "goose": {
        "cmd": ". ~/.agentlab_secrets 2>/dev/null; unset GEMINI_API_KEY; export GOOSE_DISABLE_KEYRING=1; goose session",
        "version": "goose --version", "reset": reset_none, "ready": r"goose is ready",
    },
}

# Each cell: list of steps. ("launch",) ("wait", key-of-pattern, timeout) ("dwell", s)
# ("probe", label) records starts() ("keys", ...) ("capture", label) ("kill",) ("reset",) ("edit_qwen",)
DWELL = 25
CELLS = {
    "claude-code": {
        "untrusted":       [("reset",), ("launch",), ("wait", "trust", 40), ("capture", "prompt"), ("dwell", DWELL), ("probe", "before"), ("keys", "Enter"), ("dwell", 6), ("probe", "after")],
        "after-decision":  [("reset",), ("launch",), ("wait", "trust", 40), ("keys", "Down", "Enter"), ("wait", "mcp", 30), ("capture", "prompt"), ("dwell", 10), ("probe", "before"), ("keys", "Enter"), ("dwell", 8), ("probe", "after")],
        "trusted-reopen":  [("launch",), ("settle", 20), ("capture", "screen"), ("probe", "after")],
        "positive-control":[("reset",), ("launch",), ("wait", "trust", 40), ("keys", "Down", "Enter"), ("wait", "mcp", 30), ("capture", "prompt"), ("probe", "before"), ("keys", "Up", "Up", "Enter"), ("dwell", 10), ("probe", "after")],
    },
    "gemini": {
        "untrusted":       [("reset",), ("launch",), ("wait", "trust", 50), ("capture", "prompt"), ("dwell", DWELL), ("probe", "before"), ("keys", "3", "Enter"), ("dwell", 10), ("probe", "after")],
        "after-decision":  [("reset",), ("launch",), ("wait", "trust", 50), ("capture", "prompt"), ("probe", "before"), ("keys", "1", "Enter"), ("dwell", 15), ("probe", "after"), ("settle", 25), ("probe", "late")],
        "trusted-reopen":  [("launch",), ("settle", 22), ("capture", "screen"), ("probe", "after")],
    },
    "codex": {
        "untrusted":       [("reset",), ("launch",), ("wait", "trust", 40), ("capture", "prompt"), ("dwell", DWELL), ("probe", "before"), ("keys", "Escape"), ("dwell", 6), ("probe", "after")],
        "after-decision":  [("reset",), ("launch",), ("wait", "trust", 40), ("capture", "prompt"), ("probe", "before"), ("keys", "Enter"), ("dwell", 15), ("probe", "after")],
        "trusted-reopen":  [("launch",), ("settle", 20), ("capture", "screen"), ("probe", "after")],
    },
    "cursor-agent": {
        "untrusted":       [("reset",), ("launch",), ("wait", "trust", 40), ("capture", "prompt"), ("dwell", DWELL), ("probe", "before"), ("keys", "q"), ("dwell", 5), ("probe", "after")],
        "after-decision":  [("reset",), ("launch",), ("wait", "trust", 40), ("capture", "prompt"), ("probe", "before"), ("keys", "a"), ("dwell", 15), ("probe", "after")],
        "trusted-reopen":  [("launch",), ("settle", 20), ("capture", "screen"), ("probe", "after")],
        "trust-without-mcp": [("reset",), ("launch",), ("wait", "trust", 40), ("keys", "w"), ("dwell", 12), ("probe", "before"), ("kill",),
                              ("launch",), ("wait", "mcp", 40), ("capture", "prompt"), ("dwell", 10), ("probe", "before2"), ("keys", "c"), ("dwell", 6), ("probe", "after")],
    },
    "qwen": {
        "untrusted":       [("reset",), ("launch",), ("wait", "mcp", 50), ("capture", "prompt"), ("dwell", DWELL), ("probe", "before"), ("keys", "Escape"), ("dwell", 8), ("probe", "after")],
        "reopen-after-reject": [("launch",), ("settle", 22), ("capture", "screen"), ("probe", "after")],
        "after-decision":  [("reset",), ("launch",), ("wait", "mcp", 50), ("capture", "prompt"), ("probe", "before"), ("keys", "Enter"), ("dwell", 12), ("probe", "after")],
        "trusted-reopen":  [("launch",), ("settle", 22), ("capture", "screen"), ("probe", "after")],
        "config-changed":  [("edit_qwen",), ("launch",), ("wait", "mcp", 50), ("capture", "prompt"), ("dwell", 10), ("probe", "before"), ("keys", "Escape"), ("dwell", 6), ("probe", "after")],
    },
    "goose": {
        "open-in-repo":    [("launch",), ("wait", "ready", 40), ("settle", 20), ("capture", "screen"), ("probe", "after")],
    },
}

def edit_qwen():
    p = REPO / ".qwen/settings.json"
    d = json.loads(p.read_text())
    d["mcpServers"]["q5-canary"]["env"]["CANARY_SOURCE"] = ".qwen/settings.json (changed)"
    p.write_text(json.dumps(d, indent=2) + "\n")

def run_cell(lab, client, cell, rep, outdir):
    spec = CLIENTS[client]
    row = {"client": client, "cell": cell, "rep": rep, "t_start": now(), "prompt_seen": "", "preselected": "",
           "keys": "", "starts_before": "", "starts_before2": "", "starts_after": "", "starts_late": "", "captures": "",
           "version_cell": sh(spec["version"] + " 2>&1 | head -1").stdout.strip()}
    regenerate_repo(lab)
    reset_log()
    caps = []
    for step in CELLS[client][cell]:
        op = step[0]
        if op == "reset":
            spec["reset"]()
        elif op == "launch":
            launch(spec["cmd"])
        elif op == "kill":
            kill()
        elif op == "edit_qwen":
            edit_qwen()
        elif op == "wait":
            s = wait_for(spec[step[1]], step[2])
            row["prompt_seen"] += ("" if not row["prompt_seen"] else "+") + (step[1] if s else f"no-{step[1]}")
        elif op == "dwell":
            time.sleep(step[1])
        elif op == "settle":
            settle(step[1])
        elif op == "keys":
            keys(*step[1:])
            row["keys"] += ("" if not row["keys"] else " | ") + " ".join(step[1:])
        elif op == "probe":
            row[f"starts_{step[1]}"] = starts()
        elif op == "capture":
            text = screen()
            name = f"{client}__{cell}__r{rep}__{step[1]}.txt"
            (outdir / "captures" / name).write_text(text)
            caps.append(name)
            if step[1] == "prompt" and not row["preselected"]:
                row["preselected"] = preselected(text)
    name = f"{client}__{cell}__r{rep}__end.txt"
    (outdir / "captures" / name).write_text(screen())
    caps.append(name)
    kill()
    row["captures"] = " ".join(caps)
    row["t_end"] = now()
    row["canary_log"] = LOG.read_text().strip().replace("\n", " || ")
    return row

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--out", default=str(HOME / "q5-run"))
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--clients", default=",".join(CLIENTS))
    ap.add_argument("--only", default="", help="comma list of client or client:cell, e.g. codex,gemini:after-decision")
    a = ap.parse_args()
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = Path(a.out) / run_id
    (outdir / "captures").mkdir(parents=True)
    clients = [c for c in a.clients.split(",") if c in CLIENTS]
    if a.only:
        wanted = {x.split(":")[0] for x in a.only.split(",")}
        clients = [c for c in clients if c in wanted]
    meta = {"run_id": run_id, "t_start": now(), "reps": a.reps, "clients": clients, "dwell_s": DWELL,
            "versions": {c: sh(CLIENTS[c]["version"] + " 2>&1 | head -1").stdout.strip() for c in clients},
            "os": sh("cat /etc/debian_version; uname -r").stdout.split()}
    fields = ["client", "version", "version_cell", "cell", "rep", "prompt_seen", "preselected", "keys", "starts_before",
              "starts_before2", "starts_after", "starts_late", "t_start", "t_end", "captures", "canary_log"]
    with open(outdir / "matrix.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for rep in range(1, a.reps + 1):
            for client in clients:
                for cell in CELLS[client]:
                    if a.only and client not in a.only.split(",") and f"{client}:{cell}" not in a.only.split(","):
                        continue
                    row = run_cell(a.lab, client, cell, rep, outdir)
                    row["version"] = meta["versions"][client]
                    w.writerow(row)
                    f.flush()
                    print(f"{row['t_end']} r{rep} {client:12} {cell:20} before={row['starts_before']} after={row['starts_after']} late={row['starts_late']} seen={row['prompt_seen']} v={row['version_cell']}", flush=True)
    meta["t_end"] = now()
    meta["versions_after"] = {c: sh(CLIENTS[c]["version"] + " 2>&1 | head -1").stdout.strip() for c in clients}
    (outdir / "run.json").write_text(json.dumps(meta, indent=2))
    print(f"done: {outdir}")

if __name__ == "__main__":
    main()
