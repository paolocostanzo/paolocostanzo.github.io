#!/usr/bin/env python3
"""Removes the residue of exploratory set-up runs (D16 point 8) before the clean snapshot is taken: tool approvals for
q3-lab given in each client, the server log, the control file and the set-up outputs. Prints every change it makes,
so that the cleanup itself is on record. Inside the disposable VM only.

Stores (observed in the set-up runs or read in the clients' source, see research_notes/q3-client-src/):
  codex        ~/.codex/config.toml          [mcp_servers.q3-lab.tools.q3_noop] approval_mode  (inside the prepare_vm block)
  cursor-agent ~/.cursor/cli-config.json     permissions.allow "Mcp(q3-lab:...)"
  qwen         ~/.qwen/settings.json         permissions.allow "mcp__q3-lab__..."
  claude-code  ~/.claude.json projects[*].allowedTools, ~/.claude/settings*.json permissions.allow "mcp__q3-lab__..."
  gemini       ~/.gemini/policies/auto-saved.toml (only with permanent approval enabled; session approvals are in memory)
  goose        ~/.config/goose/permission.yaml  always_allow / ask_before / never_allow entries "q3-lab__..."

  python3 cleanup_setup.py [--dry-run]
"""
import argparse, json, re, subprocess, shutil
from pathlib import Path

HOME = Path.home()
HERE = Path(__file__).resolve().parent
changes = []

def is_q3(s):
    return isinstance(s, str) and ("q3-lab" in s or "q3_lab" in s or "q3_noop" in s)

def json_filter(path, getter_paths, dry):
    p = HOME / path
    if not p.exists():
        return
    d = json.loads(p.read_text() or "{}")
    touched = False
    for keys in getter_paths:
        node = d
        for k in keys[:-1]:
            node = node.get(k, {}) if isinstance(node, dict) else {}
        last = keys[-1]
        if isinstance(node, dict) and isinstance(node.get(last), list):
            kept = [x for x in node[last] if not is_q3(x)]
            removed = [x for x in node[last] if is_q3(x)]
            if removed:
                changes.append(f"{path}: {'.'.join(keys)} -= {removed}")
                node[last] = kept
                touched = True
    if path == ".claude.json":
        for proj, v in (d.get("projects") or {}).items():
            tools = v.get("allowedTools") if isinstance(v, dict) else None
            if isinstance(tools, list) and any(is_q3(t) for t in tools):
                changes.append(f"{path}: projects[{proj}].allowedTools -= {[t for t in tools if is_q3(t)]}")
                v["allowedTools"] = [t for t in tools if not is_q3(t)]
                touched = True
    if touched and not dry:
        p.write_text(json.dumps(d, indent=2) + "\n")

def text_filter(path, pattern, dry):
    p = HOME / path
    if not p.exists():
        return
    lines = p.read_text().splitlines(keepends=True)
    kept = [l for l in lines if not re.search(pattern, l)]
    if len(kept) != len(lines):
        changes.append(f"{path}: removed lines {[l.strip() for l in lines if re.search(pattern, l)]}")
        if not dry:
            p.write_text("".join(kept))

# Session histories written by set-up runs (not reloaded automatically, removed to approximate "first use after trust").
# Folder-trust markers are kept. Codex's SQLite state/history databases are left as they are (declared in the set-up notes).
HISTORY_GLOBS = [
    ".cursor/chats/*", ".cursor/projects/home-lab-q3-repo/agent-transcripts", ".cursor/projects/home-lab-q3-repo/worker.log",
    ".codex/sessions/*/*/*", ".qwen/projects/-home-lab-q3-repo", ".qwen/tmp/*", ".gemini/tmp/q3-repo", ".gemini/history/q3-repo",
    ".local/share/goose/sessions", ".local/state/goose/logs", ".claude/projects/-home-lab-q3-repo",
]

def remove_histories(since_epoch, dry):
    for pattern in HISTORY_GLOBS:
        for path in sorted(HOME.glob(pattern)):
            if path.name == ".workspace-trusted" or path.stat().st_mtime < since_epoch:
                continue
            changes.append(f"history removed: {path.relative_to(HOME)}")
            if not dry:
                shutil.rmtree(path) if path.is_dir() else path.unlink()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--histories-since", type=float, default=None,
                    help="also remove session histories modified after this epoch time (start of the set-up runs)")
    a = ap.parse_args()
    d = a.dry_run
    if a.histories_since is not None:
        remove_histories(a.histories_since, d)
    # codex: rewrite the prepare_vm block (drops tool approvals the client added inside it)
    toml = HOME / ".codex/config.toml"
    if toml.exists() and "approval_mode" in toml.read_text():
        changes.append(".codex/config.toml: q3-lab block rewritten by prepare_vm.py (approval_mode removed)")
        if not d:
            subprocess.run(["python3", str(HERE / "prepare_vm.py")], check=True, capture_output=True)
    json_filter(".cursor/cli-config.json", [("permissions", "allow")], d)
    json_filter(".qwen/settings.json", [("permissions", "allow")], d)
    json_filter(".claude.json", [], d)
    for f in [".claude/settings.json", ".claude/settings.local.json", "q3-repo/.claude/settings.local.json", "q3-repo/.qwen/settings.json"]:
        json_filter(f, [("permissions", "allow")], d)
    text_filter(".gemini/policies/auto-saved.toml", r"q3-lab|q3_noop", d)
    text_filter("q3-repo/.gemini/policies/auto-saved.toml", r"q3-lab|q3_noop", d)
    text_filter(".config/goose/permission.yaml", r"q3-lab__|q3_lab__", d)
    for f in ["q3-server.log"]:
        if (HOME / f).exists() and (HOME / f).stat().st_size:
            changes.append(f"{f}: emptied")
            if not d:
                (HOME / f).write_text("")
    for dname in ["q3-setup", "q3-out"]:
        if (HOME / dname).exists():
            changes.append(f"{dname}/: removed (set-up outputs, already copied to the host)")
            if not d:
                shutil.rmtree(HOME / dname)
    if not d:
        (HOME / "q3-control.json").write_text(json.dumps({"state": "A", "signal": "none", "change": "description",
                                                          "declareListChanged": True, "ttlMs": 0, "cell": "clean"}) + "\n")
    changes.append("q3-control.json: reset to state A")
    print(json.dumps({"dry_run": d, "changes": changes}, indent=2))

if __name__ == "__main__":
    main()
