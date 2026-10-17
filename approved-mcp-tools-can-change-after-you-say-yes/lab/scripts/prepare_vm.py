#!/usr/bin/env python3
"""Prepares the disposable VM for Q3 (run once, before the clean snapshot is taken).

- writes the test folder ~/q3-repo (an ordinary small project; it declares no MCP server);
- adds the Q3 server, under the name q3-lab, to each client's **user-level** MCP configuration
  (deviation D16 point 2), keeping a copy of every file it changes in ~/q3-backup/;
- writes the control file in state A.
It does not answer any trust or approval question: those are given once, by hand, through each
client's own interface, and recorded in the set-up notes.

  python3 prepare_vm.py [--dry-run]
"""
import argparse, datetime as dt, json, re, shutil
from pathlib import Path

HOME = Path.home()
LAB = Path(__file__).resolve().parent.parent
SERVER = LAB / "server" / "q3-server.mjs"
REPO = HOME / "q3-repo"
CONTROL = HOME / "q3-control.json"
LOG = HOME / "q3-server.log"
BACKUP = HOME / "q3-backup"
NAME = "q3-lab"
BEGIN, END = "# >>> q3-lab (Outside the Prompt lab, Q3) >>>", "# <<< q3-lab <<<"

def env(client):
    return {"Q3_CONTROL": str(CONTROL), "Q3_LOG": str(LOG), "Q3_CLIENT": client}

def backup(p):
    if p.exists():
        BACKUP.mkdir(exist_ok=True)
        dst = BACKUP / (str(p.relative_to(HOME)).replace("/", "__") + ".orig")
        if not dst.exists():
            shutil.copy2(p, dst)

def json_server(p, client, key="mcpServers", extra=None, dry=False):
    d = json.loads(p.read_text() or "{}") if p.exists() else {}
    entry = {"command": "node", "args": [str(SERVER)], "env": env(client)}
    if extra:
        entry.update(extra)
    d.setdefault(key, {})[NAME] = entry
    if not dry:
        backup(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=2) + "\n")
    return f"{p}: {key}.{NAME}"

def strip_block(text):
    # the block is indented in YAML: remove the indentation of its first line too
    return re.sub(r"[ \t]*" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S)

def codex(dry=False):
    p = HOME / ".codex/config.toml"
    text = strip_block(p.read_text() if p.exists() else "")
    q = json.dumps
    e = ", ".join(f"{k} = {q(v)}" for k, v in env("codex").items())
    block = "\n".join([BEGIN, f"[mcp_servers.{NAME}]", 'command = "node"', f"args = [{q(str(SERVER))}]",
                       f"env = {{ {e} }}", END, ""])
    if not dry:
        backup(p)
        p.write_text(text.rstrip("\n") + "\n\n" + block)
    return f"{p}: [mcp_servers.{NAME}]"

def goose(dry=False):
    p = HOME / ".config/goose/config.yaml"
    text = strip_block(p.read_text() if p.exists() else "")
    envs = "\n".join(f"      {k}: {v}" for k, v in env("goose").items())
    block = "\n".join([f"  {BEGIN}", f"  {NAME}:", "    type: stdio", f"    name: {NAME}", "    cmd: node",
                       "    args:", f"    - {SERVER}", "    enabled: true", "    timeout: 300", "    envs:", envs,
                       f"  {END}", ""])
    lines = text.splitlines(keepends=True)
    idx = next((i for i, l in enumerate(lines) if l.rstrip() == "extensions:"), None)
    if idx is None:
        new = text.rstrip("\n") + "\nextensions:\n" + block
    else:
        new = "".join(lines[: idx + 1]) + block + "".join(lines[idx + 1:])
    if not dry:
        backup(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(new)
    return f"{p}: extensions.{NAME}"

def repo(dry=False):
    if dry:
        return f"{REPO}: README.md, greet.py"
    REPO.mkdir(exist_ok=True)
    (REPO / "README.md").write_text("# q3-demo\n\nSmall demo project for the Q3 lab (tool change after approval).\n"
                                    "It declares no MCP server; the lab server is configured at user level.\n")
    (REPO / "greet.py").write_text('def greet(name):\n    return f"hello, {name}"\n')
    return f"{REPO}: README.md, greet.py"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    d = a.dry_run
    done = [
        repo(d),
        json_server(HOME / ".claude.json", "claude-code", extra={"type": "stdio"}, dry=d),
        json_server(HOME / ".gemini/settings.json", "gemini", dry=d),
        json_server(HOME / ".qwen/settings.json", "qwen", dry=d),
        json_server(HOME / ".cursor/mcp.json", "cursor-agent", dry=d),
        codex(d),
        goose(d),
    ]
    if not d:
        CONTROL.write_text(json.dumps({"state": "A", "signal": "none", "change": "description",
                                       "declareListChanged": True, "ttlMs": 0, "cell": "prepare"}) + "\n")
        LOG.write_text("")
    print(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "server": str(SERVER), "dry_run": d,
                      "configured": done}, indent=2))

if __name__ == "__main__":
    main()
