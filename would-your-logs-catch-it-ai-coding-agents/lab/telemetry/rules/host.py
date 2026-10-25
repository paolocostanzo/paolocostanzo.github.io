#!/usr/bin/env python3
"""Rules on host telemetry (D27 point 5, D13 point 4; reported apart from the H5 verdict), one per event type, and their tooling.

Source: the VM's audit log (`telemetry/audit.log` of each run or session): execve/execveat of the lab user and the working
directory of each process start. The journal and the outgoing-connection lines were explored on development material and carry
nothing about these events (NOTE-HOST-TELEMETRY.md in the campaign notes).

  - host/q5-start (H-Q5): an AI client process directly starts an MCP launcher (an interpreter or runner, not a shell), in a
    project folder (a folder in a home folder that is not hidden), within 30 s of the client's own start, and the launcher's
    command line (digits replaced by N) has not been seen before as a child of an AI client on this host: neither in the learning
    period (the set of commands learned from development sessions only, frozen with the rules) nor earlier in the same session.
  - host/q3-announced, host/q3-silent: none. The definition changes inside a server process that is already running and the
    notification travels over stdio: no process starts, no watched file is written, no connection is opened.
The rule names no lab artefact (D27 point 6): client binaries and launchers are public product and interpreter names; the learned
set is data, built from normal use.

  python3 host.py learn SESSION_DIR... > host-known-commands.json     (development sessions only, inside their windows)
  python3 host.py apply KNOWN.json AUDIT_LOG T0 T1                   ({"host/q5-start": [hit times]})
"""
import json, re, sys, tarfile
from pathlib import Path

REC = re.compile(r"type=(\w+) msg=audit\((\d+\.\d+):(\d+)\): (.*)")
KV = re.compile(r'(?:^|(?<=\s))(\w+)=("[^"]*"|\S+)')  # anchored after a space: no quadratic backtracking on long records
KVA = re.compile(r'(?:^|(?<=\s))(a\d+(?:\[\d+\])?)=("[^"]*"|\S+)')  # EXECVE arguments, whole or in pieces
EXEC = (59, 322)  # execve, execveat (x86_64)
CLIENT_BINS = {"claude", "codex", "gemini", "qwen", "goose", "cursor-agent", "cursor"}
MCP_LAUNCHERS = {"node", "nodejs", "npx", "bunx", "bun", "deno", "python", "python3", "uv", "uvx", "pipx",
                 "docker", "podman", "java", "ruby", "go"}
# a project folder: a folder inside a home folder that is not hidden (the home folder itself, its hidden folders, /, /tmp and
# system folders are not project folders)
PROJECT_RX = re.compile(r"^(/home/[^/]+|/root)/[^./][^/]*(/.*)?$")
START_S = 30
RULES = {"host/q5-start": "H-Q5", "host/q3-announced": None, "host/q3-silent": None}


def unq(v):
    if v is None:
        return None
    if v.startswith('"'):
        return v.strip('"')
    try:  # hex-encoded values (arguments with spaces or special characters)
        return bytes.fromhex(v).decode("utf-8", "replace").replace("\0", " ")
    except ValueError:
        return v


def events(path, t0=float("-inf"), t1=float("inf")):
    """Audit events (records grouped by serial) with time in [t0, t1], in time order: SYSCALL with argv and cwd."""
    ev = {}
    with open(path, errors="replace") as f:
        for line in f:
            ty = line[5:line.find(" ", 5)]
            if ty not in ("SYSCALL", "EXECVE", "CWD"):
                continue
            m = REC.match(line)
            if not m:
                continue
            t = float(m.group(2))
            if t < t0 or t > t1:
                continue
            e = ev.setdefault((m.group(2), m.group(3)), {"t": t, "args": {}, "argc": None})
            kv = dict(KV.findall(m.group(4).split("\x1d")[0]))
            if ty == "SYSCALL":
                e.update(type="SYSCALL", syscall=int(kv.get("syscall", -1)), ok=kv.get("success") == "yes",
                         pid=int(kv.get("pid", 0)), ppid=int(kv.get("ppid", 0)), comm=unq(kv.get("comm")),
                         exe=unq(kv.get("exe")))
            elif ty == "EXECVE":  # long arguments come as a0_len=… a0[0]=… a0[1]=… on continuation lines
                if "argc" in kv:
                    e["argc"] = int(kv["argc"])
                for k, v in KVA.findall(m.group(4).split("\x1d")[0]):
                    i, part = re.match(r"a(\d+)(?:\[(\d+)\])?$", k).groups()
                    e["args"].setdefault(int(i), {})[int(part) if part else -1] = unq(v) or ""
            elif ty == "CWD":
                e["cwd"] = unq(kv.get("cwd"))
    out = []
    for e in ev.values():
        if e.get("type") != "SYSCALL":
            continue
        n = e["argc"] if e["argc"] is not None else len(e["args"])
        e["argv"] = ["".join(v for _, v in sorted(e["args"].get(i, {}).items())) for i in range(n)]
        out.append(e)
    return sorted(out, key=lambda e: e["t"])


def base(s):
    return (s or "").rstrip("/").split("/")[-1]


def is_client(img):
    exe, argv = img
    return bool(({base(exe)} | {base(a) for a in argv[:2]} | set((exe or "").split("/"))) & CLIENT_BINS)


class Tree:
    """Image (exe, argv) and exec time per pid; a fork without exec inherits its parent's."""
    def __init__(self):
        self.img, self.parent, self.texec = {}, {}, {}

    def feed(self, e):
        if self.parent.get(e["pid"], e["ppid"]) != e["ppid"]:  # the pid was reused (another boot or a new process)
            self.img.pop(e["pid"], None)
            self.texec.pop(e["pid"], None)
        self.parent[e["pid"]] = e["ppid"]
        if e["syscall"] in EXEC and e["ok"]:
            self.img[e["pid"]] = (e["exe"], e["argv"] or [e["comm"] or ""])
            self.texec[e["pid"]] = e["t"]

    def owner(self, pid, depth=4):
        while pid and depth and pid not in self.img:
            pid, depth = self.parent.get(pid), depth - 1
        return pid if pid in self.img else None

    def image(self, pid):
        o = self.owner(pid)
        return self.img[o] if o else (None, [])

    def exec_time(self, pid):
        return self.texec.get(self.owner(pid))


def norm_cmd(argv):
    return re.sub(r"\d+", "N", " ".join(argv))


def launches(evs):
    """(t, normalised command, cwd, seconds since the parent client's exec, pid) for each MCP launcher an AI client starts."""
    tree, out = Tree(), []
    for e in evs:
        if e["syscall"] in EXEC and e["ok"]:
            parent, img = tree.image(e["ppid"]), (e["exe"], e["argv"])
            if is_client(parent) and not is_client(img) and (
                    base(e["exe"]) in MCP_LAUNCHERS or base((e["argv"] or [""])[0]) in MCP_LAUNCHERS):
                pt = tree.exec_time(e["ppid"])
                out.append((e["t"], norm_cmd(e["argv"]), e.get("cwd") or "", None if pt is None else e["t"] - pt, e["pid"]))
        tree.feed(e)
    return out


def session_window(folder):
    """t_start, t_end of the session.json in a session's (or attempt's) vm-out.tgz."""
    with tarfile.open(Path(folder) / "vm-out.tgz") as t:
        name = next(n for n in t.getnames() if n.startswith("q4-out/") and n.endswith("/session.json"))
        s = json.load(t.extractfile(name))
    return s["t_start"], s["t_end"]


def learn(session_dirs):
    """Commands AI clients started inside the sessions' own windows: the log's older history (from the snapshot's making) is
    not normal use and is left out (adversarial review of D36, H1)."""
    known = set()
    for d in session_dirs:
        t0, t1 = session_window(d)
        known |= {cmd for t, cmd, _, _, _ in launches(events(Path(d) / "telemetry/audit.log")) if t0 <= t <= t1}
    return sorted(known)


def apply(known, audit_log, t0, t1):
    """{rule id: [hit times in [t0, t1]]}. The whole log up to t1 is read so that the process tree is complete; only launches
    in [t0, t1] are considered, and "seen before" is the learning set or a launch earlier in [t0, t1]."""
    evs = [e for e in events(audit_log) if e["t"] <= t1]
    seen, hits = set(known), []
    for t, cmd, cwd, dt, _ in launches(evs):
        if t < t0:
            continue
        if PROJECT_RX.match(cwd) and dt is not None and dt <= START_S and cmd not in seen:
            hits.append(t)
        seen.add(cmd)
    return {"host/q5-start": hits, "host/q3-announced": [], "host/q3-silent": []}


def exec_time_of(audit_log, pid, near, slack=60):
    """The time of the last successful exec of pid at or before `near`, within slack seconds, or None: the start of the process
    that the ground truth names (D36)."""
    ts = [e["t"] for e in events(audit_log, near - slack, near) if e["pid"] == pid and e["syscall"] in EXEC and e["ok"]]
    return max(ts) if ts else None


def coverage(audit_log, t0, t1, size=60):
    """One-minute windows of [t0, t1] holding at least one audit record of a process start (D31 point 2): the audit log rotates
    within an hour-long session and only the current file is copied, so early windows can hold no record (D36)."""
    n = int((t1 - t0) // size)
    return len({int((e["t"] - t0) // size) for e in events(audit_log, t0, t0 + n * size) if e["syscall"] in EXEC
                and e["t"] < t0 + n * size})


if __name__ == "__main__":
    if sys.argv[1] == "learn":
        print(json.dumps({"_note": "commands started by AI clients inside development sessions (host/q5-start learning set)",
                          "sessions": sys.argv[2:], "known": learn(sys.argv[2:])}, indent=1))
    elif sys.argv[1] == "apply":
        known = json.load(open(sys.argv[2]))["known"]
        print(json.dumps(apply(known, sys.argv[3], float(sys.argv[4]), float(sys.argv[5])), indent=1))
