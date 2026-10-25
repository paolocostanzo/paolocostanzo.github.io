#!/usr/bin/env python3
"""Secret gate for the open-weight cells (D39), the same gate as q4_events.py (D26): take fresh fingerprints of the VM's secrets
(never their values) into the host's fingerprint state, then scan every file copied out for the campaign, archives included, by
exact value and by format. Fails closed: a finding, a file that cannot be scanned or fingerprints that cannot be trusted make the
campaign not publishable. Writes secret-gate.json (files and kinds only, never values) in the campaign folder.

    python3 open_weight_gate.py --out ../../results/q3/open-weight-20261024 --vm-ip 192.168.122.70
The VM must be running (any restore of the campaign's snapshot: its secret stores are the ones the cells used).
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "telemetry" / "scripts"))
import secret_gate as sg  # noqa: E402
import q3_host as qh  # noqa: E402

STATE = Path.home() / ".agentlab-q4"  # shared with q4_events.py, outside every results folder: never published


def manifest(out):
    """SHA-256 of every file in the campaign folder except the gate's own report: the approval holds for exactly these bytes."""
    import hashlib
    report = out / "secret-gate.json"
    files = sorted(p for p in out.rglob("*") if p.is_file() and p != report)
    return {os.path.relpath(p, out): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def write_report(out, result):
    tmp = out / "secret-gate.json.tmp"
    tmp.write_text(json.dumps(result, indent=1))
    os.replace(tmp, out / "secret-gate.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--vm-ip", required=True)
    a = ap.parse_args()
    out = Path(a.out).resolve()
    # any earlier approval is withdrawn first: until this run ends cleanly, the campaign is not publishable
    write_report(out, {"made": qh.now(), "folder": out.name, "publishable": False, "state": "gate running or failed"})
    problems, findings, failures, files = [], [], [], {}
    try:
        files = manifest(out)
        gate = Path(__file__).resolve().parents[2] / "telemetry" / "scripts" / "secret_gate.py"
        r = subprocess.run(["ssh", "-i", str(qh.KEY), "-o", f"UserKnownHostsFile={qh.KNOWN}", "-o", "BatchMode=yes",
                            f"lab@{a.vm_ip}", "python3 - fingerprints"], input=gate.read_text(), capture_output=True,
                           text=True, timeout=120)
        try:
            new = json.loads(r.stdout)
            problems += sg.fingerprint_problems(new)
        except ValueError:
            new = None
            problems.append(f"no fingerprints from the VM (exit {r.returncode})")
        fp = STATE / "secret-fingerprints.json"
        old = json.loads(fp.read_text()) if fp.exists() else None
        if old is not None and sg.fingerprint_problems(old, require_kinds=()):
            problems.append("host fingerprint state malformed")
        if not problems:
            merged = sg.merge_fingerprints(old, new)
            STATE.mkdir(exist_ok=True)
            fp.write_text(json.dumps(merged))
            os.chmod(fp, 0o600)
            findings, failures = sg.scan_paths([out], merged["fingerprints"])
            if manifest(out) != files:
                problems.append("campaign files changed during the scan")
    except Exception as e:   # fail closed
        problems.append("gate error: " + type(e).__name__)
    result = {"made": qh.now(), "folder": out.name, "fingerprint_problems": problems,
              "findings": [{"path": os.path.relpath(f["path"], out), "member": f.get("member"), "kind": f["kind"]} for f in findings],
              "unscannable": [{"path": os.path.relpath(x["path"], out), "member": x.get("member"), "error": x.get("error")}
                              for x in failures],
              "manifest": files,
              "publishable": not problems and not findings and not failures and bool(files)}
    write_report(out, result)
    print(json.dumps({k: (len(v) if k in ("findings", "manifest") else v) for k, v in result.items()}))
    sys.exit(0 if result["publishable"] else 9)


def still_valid(out):
    """True only if the report approves the campaign and every file is the one that was scanned (for the publishing step)."""
    out = Path(out).resolve()
    try:
        r = json.loads((out / "secret-gate.json").read_text())
    except (OSError, ValueError):
        return False
    return bool(r.get("publishable")) and r.get("manifest") == manifest(out)


if __name__ == "__main__":
    main()
