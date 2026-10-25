#!/usr/bin/env python3
"""Second, independent reading of the RQ4 per-tool census by Codex CLI (deviation D22, point 2).

For each repository: clone it at the pinned commit on the host, check the commit, then run one fresh Codex session in the isolated
container of ../../tool-change/scripts/isolated_codex.py with the clone mounted read-only at /work/src. Codex gets the rules below
verbatim and returns one row per registered tool name. The first census is never mounted or quoted. A reading counts only if the
isolation probe and the initial-context check pass.

  python3 census_codex.py --config CONFIG.json --pinned pinned-commits.csv --auth ~/.codex/auth.json --out OUT_DIR
                          [--model gpt-6.1-sol --effort medium]

CONFIG.json: [{"repo": "owner/name", "pinned_name": "<name in pinned-commits.csv>", "url": "https://github.com/owner/name.git",
               "commit": "<sha>", "servers": [{"name": "<server>", "path": "<folder in the repo>"}]}]
Every source is a fresh fetch of exactly the pinned commit into OUT_DIR/sources (never an existing checkout); the working tree
must be clean, with no untracked or ignored files, and a manifest with the SHA-256 of every mounted file is kept.
"""
import argparse, csv, hashlib, importlib.util, json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("isolated_codex", HERE.parents[1] / "tool-change/scripts/isolated_codex.py")
iso = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(iso)

RULES = """Census rules (deviation D17 points 2-4, and the operational definitions of D22 point 2), given verbatim:

- One row per tool name the server exposes: every tool registered with its MCP SDK (including tools registered only under a flag
  or a toolset), and every named tool reachable through a dispatcher or catalog tool (for example a pair such as "search tools" /
  "execute tool" that lists and runs catalog entries). Deprecated aliases that reuse another tool's definition are not counted
  separately; a tool with its own name and its own schema is counted even if it reuses another tool's handler. Give the file:line
  of the registration or of the catalog entry, and say in class_reason whether the tool is direct or catalog-only.
- Class A: the tool returns free prose written by third parties (titles, descriptions, bodies, comments, messages, file contents,
  logs). Names and identifier-like strings (names of branches, repositories, projects, pipelines, teams, users, display names,
  tags or labels used as identifiers) do not make a tool class A. Otherwise the class is B.
- Vendor-only prose: the only prose the tool returns is written by the vendor itself (documentation, curated advisories). Such prose
  counts as qualifying prose for class and protection: the tool is class A and gets a protection label like any other, and
  vendor_only is true. A tool that returns both vendor and third-party prose is class A with vendor_only false.
- Protection, for class A tools, refers to the server's own output-side defence against untrusted content (for example a
  delimiter or boundary around untrusted data, character hygiene, or provenance marking applied to the tool result):
  full = all the qualifying prose the tool can return passes through the defence;
  partial = only part of it does, including when an error branch or a secondary branch returns qualifying prose outside the defence;
  none = none of it does.
  For class B tools, protection is n-a.
- Mechanism: name the defence function or wrapper and how it is applied (or "none").
- Evidence: file:line for the registration, for where the prose is returned, and for the defence call (or its absence).
"""

TASK = """You are reading open-source code for a research census. The repository is mounted read-only at /work/src at commit {commit}.
Census these servers only:
{servers}
Read the source with the shell tools available (read-only). Do not guess: every label needs file:line evidence from /work/src.

{rules}
Return the JSON object requested by the output schema: "rows", one object per registered tool of the listed servers."""

ROW = {"type": "object", "additionalProperties": False,
       "properties": {k: {"type": "string"} for k in ("server", "tool", "registered_at", "class", "class_reason", "protection",
                                                      "mechanism", "evidence")} | {"vendor_only": {"type": "boolean"}},
       "required": ["server", "tool", "registered_at", "class", "class_reason", "protection", "mechanism", "evidence", "vendor_only"]}
ROW["properties"]["class"] = {"type": "string", "enum": ["A", "B"]}
ROW["properties"]["protection"] = {"type": "string", "enum": ["full", "partial", "none", "n-a"]}
SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"rows": {"type": "array", "items": ROW}},
          "required": ["rows"]}
FIELDS = ["repo", "commit", "server", "tool", "registered_at", "class", "class_reason", "protection", "mechanism", "vendor_only", "evidence",
          "consistent"]


def fresh_source(url, commit, dest: Path) -> list[dict]:
    """Fetch exactly `commit` into a new directory, check it and return the manifest of the files that will be mounted."""
    if dest.exists():
        sys.exit(f"{dest} already exists: sources must be fresh")
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "fetch", "-q", "--depth", "1", url, commit], check=True)
    subprocess.run(["git", "-C", str(dest), "-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"], check=True)
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if head != commit:
        sys.exit(f"{dest}: HEAD {head} is not the pinned commit {commit}")
    dirty = subprocess.run(["git", "-C", str(dest), "status", "--porcelain", "--untracked-files=all", "--ignored"],
                           capture_output=True, text=True, check=True).stdout
    if dirty.strip():
        sys.exit(f"{dest}: working tree not clean:\n{dirty[:500]}")
    manifest = []
    for f in sorted(x for x in dest.rglob("*") if x.is_file() and ".git" not in x.relative_to(dest).parts[:1]):
        manifest.append({"path": str(f.relative_to(dest)), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
    return manifest


def check_pinned(cfg, pinned_csv):
    pinned = {r["repo"]: r["commit"] for r in csv.DictReader(open(pinned_csv, newline=""))}
    for entry in cfg:
        if pinned.get(entry.get("pinned_name")) != entry["commit"]:
            sys.exit(f"{entry['repo']}: commit {entry['commit']} is not the pinned commit for {entry.get('pinned_name')!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--pinned", required=True, help="pinned-commits.csv of the census")
    ap.add_argument("--auth", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="gpt-6.1-sol")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text())
    check_pinned(cfg, a.pinned)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    empty_src = out / "sources" / "_reference"
    empty_src.mkdir(parents=True)
    ref = iso.reference_session(out / "sessions" / "_reference", Path(a.auth), a.model, a.effort, SCHEMA,
                                ro_binds=((empty_src, "/work/src"),))
    meta = {"model": a.model, "effort": a.effort, "rules": RULES, "schema": SCHEMA, "reference": ref, "sessions": []}
    (out / "second-reading.json").write_text(json.dumps(meta, indent=2))
    if not ref["ok"]:
        sys.exit(f"reference session failed, nothing read: {ref['context']['problems'] or ref['record'].get('status')}")
    with open(out / "second-reading.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS + ["valid"], extrasaction="ignore")
        w.writeheader()
        for entry in cfg:
            name = entry["repo"].replace("/", "__")
            src = out / "sources" / name
            manifest = fresh_source(entry["url"], entry["commit"], src)
            (out / "sources" / f"{name}.manifest.json").write_text(json.dumps(manifest, indent=1))
            servers = "\n".join(f"- {s['name']}: folder {s['path']}" for s in entry["servers"])
            prompt = TASK.format(commit=entry["commit"], servers=servers, rules=RULES)
            root = out / "sessions" / name
            rec = iso.run_session(prompt, root, Path(a.auth), a.model, a.effort, SCHEMA, ro_binds=((src, "/work/src"),),
                                  timeout=a.timeout)
            ctx = (iso.check_context(root / rec["rollouts"][0], prompt, ref["developer_hashes"], ref["environment"]) if rec.get("rollouts")
                   else {"ok": False, "problems": ["no rollout"], "developer_tags": [], "developer_hashes": []})
            got = []
            try:
                got = json.loads(rec.get("reply") or "")["rows"]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
            unchanged = fresh_manifest_matches(src, manifest)
            valid = rec.get("status") == "completed" and rec["probe"]["ok"] and ctx["ok"] and bool(got) and unchanged
            inconsistent = 0
            for r in got:
                # class B should have protection n-a and class A should not. A contradictory row keeps its labels untouched,
                # stays in the comparison (validity is about the session) and is flagged for adjudication.
                consistent = (r.get("class") == "B") == (r.get("protection") == "n-a")
                inconsistent += not consistent
                w.writerow({"repo": entry["repo"], "commit": entry["commit"], **r,
                            "vendor_only": str(r.get("vendor_only")).lower(), "consistent": consistent, "valid": valid})
            fh.flush()  # checkpoint after every repository
            meta["sessions"].append({"repo": entry["repo"], "commit": entry["commit"], "status": rec.get("status"),
                                     "probe": rec["probe"], "context": ctx, "source_unchanged": unchanged, "valid": valid,
                                     "rows_inconsistent": inconsistent,
                                     "n_rows": len(got), "n_files_mounted": len(manifest), "rollouts": rec.get("rollouts", [])})
            (out / "second-reading.json").write_text(json.dumps(meta, indent=2))
            print(entry["repo"], len(got), "rows", "valid" if valid else "INVALID", flush=True)


def fresh_manifest_matches(src: Path, manifest: list[dict]) -> bool:
    """The mounted source is read-only in the container; checked again on the host after the session."""
    now = sorted(x for x in src.rglob("*") if x.is_file() and ".git" not in x.relative_to(src).parts[:1])
    if [str(f.relative_to(src)) for f in now] != [m["path"] for m in manifest]:
        return False
    return all(hashlib.sha256(f.read_bytes()).hexdigest() == m["sha256"] for f, m in zip(now, manifest))


if __name__ == "__main__":
    main()
