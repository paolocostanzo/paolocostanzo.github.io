#!/usr/bin/env python3
"""Runs one Codex CLI session in a bubblewrap container that cannot see the project or the real home directory (deviation D22).

The container holds only: read-only /usr and /etc (and the usual /bin, /lib links), a private /tmp, /proc and /dev, the native
Codex executable (read-only, /opt/codex), a fresh CODEX_HOME with a copy of the authentication file and a minimal configuration,
and the working directory (/work), plus any explicit read-only source mount. The environment is cleared. Nothing else of the host
filesystem is mounted, so neither the CLI nor any child process can read the project, earlier Codex sessions, global AGENTS.md,
memories, skills or hooks. The copied credential is deleted on every exit path.

Initial-context check (D22 point 3): before the first assistant message the rollout may contain only (a) developer blocks that
are byte-identical to those of a reference session run in the same batch on a synthetic prompt (the CLI's own built-in texts for
this version and container), (b) exactly one <environment_context> block, parsed strictly, equal to the reference one except for the date, and
(c) exactly one user message equal to the prompt. Anything else makes the session invalid.

Used by code_q3_codex.py (RQ3) and by defence-coverage/scripts/census_codex.py (RQ4). Requires bwrap and the native Codex binary.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

VENDOR = Path.home() / ".local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl"
HOST_HOME = str(Path.home())
INNER_HOME = "/home/coder"
INNER_CODEX_HOME = f"{INNER_HOME}/.codex"
# Developer blocks that Codex CLI 0.162.0 sends on its own (observed in an isolated session on 17 Oct 2026): the bundled skills
# list (with the permissions and collaboration-mode sections), and two fixed multi-agent texts that it still sends with the
# multi_agent feature disabled. The tags are a first filter; the binding check is the byte-identity with the reference session.
ALLOWED_DEVELOPER_TAGS = {"permissions instructions", "collaboration_mode", "apps_instructions", "skills_instructions",
                          "multi_agent_role", "multi_agent_mode"}
ENV_FIELDS = {"cwd", "shell", "current_date", "timezone", "filesystem"}


def minimal_config(model: str, effort: str) -> str:
    # multi-agent off; no MCP servers, no profiles, no project trust entries.
    return f'model = "{model}"\nmodel_reasoning_effort = "{effort}"\n\n[features]\nmulti_agent = false\n'


def make_codex_home(root: Path, auth: Path, model: str, effort: str) -> Path:
    home = root / "codex-home"
    home.mkdir(parents=True)
    try:
        shutil.copy2(auth, home / "auth.json")
        os.chmod(home / "auth.json", 0o600)
        (home / "config.toml").write_text(minimal_config(model, effort))
    except BaseException:
        (home / "auth.json").unlink(missing_ok=True)  # never leave a credential copy behind a failed set-up
        raise
    return home


def bwrap_argv(codex_home: Path, workdir: Path, ro_binds: tuple[tuple[Path, str], ...] = (), vendor: Path = VENDOR) -> list[str]:
    argv = ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--clearenv",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
            "--ro-bind", "/etc", "/etc", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--ro-bind", str(vendor), "/opt/codex",
            "--tmpfs", "/home", "--dir", INNER_HOME,
            "--bind", str(codex_home), INNER_CODEX_HOME,
            "--bind", str(workdir), "/work"]
    for src, dst in ro_binds:
        argv += ["--ro-bind", str(src), dst]
    argv += ["--setenv", "HOME", INNER_HOME, "--setenv", "CODEX_HOME", INNER_CODEX_HOME,
             "--setenv", "PATH", "/opt/codex/bin:/opt/codex/codex-path:/usr/bin:/bin", "--setenv", "LANG", "C.UTF-8",
             "--chdir", "/work"]
    return argv


def codex_exec_args(model: str, effort: str, schema_inner: str | None, reply_inner: str = "/work/reply.txt") -> list[str]:
    args = ["/opt/codex/bin/codex", "exec", "-s", "read-only", "-c", 'approval_policy="never"', "--json", "-o", reply_inner,
            "--disable", "apps", "--disable", "plugins", "--disable", "multi_agent", "-c", "notify=[]", "--skip-git-repo-check",
            "-m", model, "-c", f'model_reasoning_effort="{effort}"']
    if schema_inner:
        args += ["--output-schema", schema_inner]
    return args + ["-"]


def probe(codex_home: Path, workdir: Path, ro_binds=(), vendor: Path = VENDOR) -> dict:
    """Inside the same container, the host home, the project and the real ~/.codex must not exist."""
    targets = [HOST_HOME, f"{HOST_HOME}/Desktop/redazione", f"{HOST_HOME}/.codex", f"{HOST_HOME}/.claude", "/run/user"]
    script = "for p in " + " ".join(f"'{t}'" for t in targets) + "; do if [ -e \"$p\" ]; then echo \"VISIBLE $p\"; fi; done; echo done"
    try:
        r = subprocess.run(bwrap_argv(codex_home, workdir, ro_binds, vendor) + ["/bin/sh", "-c", script],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "visible": [], "returncode": None, "stdout": "", "stderr": str(exc)}
    visible = [l[len("VISIBLE "):] for l in r.stdout.splitlines() if l.startswith("VISIBLE ")]
    ok = r.returncode == 0 and r.stdout.strip().endswith("done") and not visible
    return {"ok": ok, "visible": visible, "returncode": r.returncode, "stdout": r.stdout, "stderr": r.stderr[-2000:]}


def run_session(prompt: str, root: Path, auth: Path, model: str, effort: str, schema: dict | None = None,
                ro_binds=(), timeout: int = 900, vendor: Path = VENDOR) -> dict:
    """One fresh session. Never raises for a failed session: returns a record with status probe-failed, timeout, error,
    failed or completed. The caller decides validity with check_context()."""
    workdir = root / "work"
    workdir.mkdir(parents=True)
    record = {"model": model, "effort": effort, "prompt": prompt, "rollouts": []}
    codex_home = root / "codex-home"  # known before any credential is copied, so the finally block always covers it
    try:
        make_codex_home(root, auth, model, effort)
        if schema is not None:
            (workdir / "schema.json").write_text(json.dumps(schema))
        pr = probe(codex_home, workdir, ro_binds, vendor)
        record["probe"] = pr
        if not pr["ok"]:
            record["status"] = "probe-failed"
            return record
        argv = bwrap_argv(codex_home, workdir, ro_binds, vendor) + codex_exec_args(model, effort, "/work/schema.json" if schema else None)
        record["argv"] = argv
        (root / "argv.json").write_text(json.dumps(argv, indent=1))
        with open(root / "events.jsonl", "wb") as out, open(root / "stderr.txt", "wb") as err:
            p = subprocess.run(argv, input=prompt.encode(), stdout=out, stderr=err, timeout=timeout)
        record["exit_code"] = p.returncode
        reply = workdir / "reply.txt"
        record["reply"] = reply.read_text() if reply.exists() else None
        record["status"] = "completed" if p.returncode == 0 and record["reply"] else "failed"
    except subprocess.TimeoutExpired:
        record["status"] = "timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        record["status"] = "error"
        record["error"] = str(exc)
    finally:
        if codex_home.exists():
            (codex_home / "auth.json").unlink(missing_ok=True)  # never keep a credential copy next to the evidence
            record["rollouts"] = [str(r.relative_to(root)) for r in sorted(codex_home.glob("sessions/*/*/*/rollout-*.jsonl"))]
        record.setdefault("probe", {"ok": False, "visible": [], "returncode": None, "stdout": "", "stderr": "not run"})
    return record


def initial_messages(rollout: Path) -> list[tuple[str, str]]:
    """(role, text) of every message before the first assistant message."""
    out = []
    for line in rollout.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        p = d.get("payload")
        if d.get("type") == "response_item" and isinstance(p, dict) and p.get("type") == "message":
            if p.get("role") == "assistant":
                break
            text = "".join(c.get("text", "") for c in p.get("content", []) if isinstance(c, dict))
            out.append((p.get("role"), text))
    return out


def developer_hashes(rollout: Path) -> list[str]:
    return [hashlib.sha256(t.encode()).hexdigest() for role, t in initial_messages(rollout) if role == "developer"]


def parse_env(text: str) -> tuple[dict | None, list[str]]:
    """Parse one <environment_context> block strictly: only the allowed fields, each at most once, nothing else in between."""
    m = re.fullmatch(r"\s*<environment_context>(.*)</environment_context>\s*", text, re.S)
    if not m:
        return None, ["environment block is not a single <environment_context> element"]
    body, fields, problems = m.group(1), {}, []
    for el in re.finditer(r"<([a-z_]+)>(.*?)</\1>", body, re.S):
        name = el.group(1)
        if name not in ENV_FIELDS:
            problems.append(f"unexpected environment field {name!r}")
        if name in fields:
            problems.append(f"duplicate environment field {name!r}")
        fields[name] = el.group(2)
    leftover = re.sub(r"<([a-z_]+)>(.*?)</\1>", "", body, flags=re.S)
    if leftover.strip():
        problems.append("text outside the environment fields")
    if fields.get("cwd") != "/work":
        problems.append("environment cwd is not /work")
    if "current_date" in fields and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fields["current_date"]):
        problems.append("environment current_date is not a date")
    return fields, problems


def check_context(rollout: Path, prompt: str, expected_developer: list[str] | None = None,
                  expected_env: dict | None = None) -> dict:
    """See the module docstring. With expected_developer=None only the structural checks run (used for the reference session
    itself); a coding session must pass expected_developer from the batch's reference session."""
    try:
        msgs = initial_messages(rollout)
    except (OSError, UnicodeDecodeError, ValueError) as exc:  # truncated or malformed rollout: the session is excluded, not the batch
        return {"ok": False, "problems": [f"unreadable rollout: {exc}"], "developer_tags": [], "developer_hashes": [], "environment": {}}
    problems, tags, envs, prompts, env = [], [], 0, 0, None
    for role, text in msgs:
        if HOST_HOME in text:
            problems.append(f"{role} message mentions the host home directory")
        if "AGENTS.md instructions" in text:
            problems.append("AGENTS.md instructions present")
        if role == "developer":
            head = text.lstrip()[:80]
            tag = head[1:head.index(">")] if head.startswith("<") and ">" in head else None
            tags.append(tag)
            if tag not in ALLOWED_DEVELOPER_TAGS:
                problems.append(f"developer block not allowlisted: {tag or head[:40]!r}")
        elif role == "user":
            if text.lstrip().startswith("<environment_context>"):
                envs += 1
                env, env_problems = parse_env(text)
                problems += env_problems
            elif text.strip() == prompt.strip():
                prompts += 1
            else:
                problems.append("user message other than the prompt and the environment context")
        else:
            problems.append(f"unexpected role before the first answer: {role!r}")
    if envs != 1:
        problems.append(f"expected exactly one environment block, found {envs}")
    if prompts != 1:
        problems.append(f"expected exactly one prompt message, found {prompts}")
    hashes = [hashlib.sha256(t.encode()).hexdigest() for role, t in msgs if role == "developer"]
    if expected_developer is not None and hashes != expected_developer:
        problems.append("developer blocks differ from the reference session of this batch")
    stable = {k: v for k, v in (env or {}).items() if k != "current_date"}
    if expected_env is not None and stable != expected_env:
        problems.append("environment block differs from the reference session of this batch")
    return {"ok": not problems, "problems": problems, "developer_tags": tags, "developer_hashes": hashes, "environment": stable}


def reference_session(root: Path, auth: Path, model: str, effort: str, schema: dict | None, ro_binds=(),
                      vendor: Path = VENDOR) -> dict:
    """A session on a synthetic prompt, in the same container layout, whose developer blocks become the batch baseline."""
    prompt = ("Reference session for an isolation check. There is no task. "
              + ('Return {"outcome": "stale", "reason": "reference"}.' if schema and "outcome" in json.dumps(schema)
                 else 'Return {"rows": []}.'))
    rec = run_session(prompt, root, auth, model, effort, schema, ro_binds, timeout=600, vendor=vendor)
    ctx = (check_context(root / rec["rollouts"][0], prompt) if rec.get("rollouts")
           else {"ok": False, "problems": ["no rollout"], "developer_tags": [], "developer_hashes": []})
    return {"record": {k: v for k, v in rec.items() if k != "prompt"}, "context": ctx,
            "ok": rec.get("status") == "completed" and rec["probe"]["ok"] and ctx["ok"],
            "developer_hashes": ctx["developer_hashes"], "environment": ctx.get("environment")}
