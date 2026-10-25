#!/usr/bin/env python3
"""Host-side orchestration of the open-weight cells (D39), on top of q3_host.py.

For every cell: restore the clean snapshot, start the VM, copy the harness in, start the recording proxy inside the VM
(record_proxy.py, 127.0.0.1:11435 -> the model endpoint reachable from the VM), run one cell with the open-weight client file,
stop the proxy's output by fetching it with the cell, copy everything out, stop the VM at the end.

The model endpoint is reached through a reverse SSH tunnel that this script opens from the host into the VM after each restore
(VM 127.0.0.1:11434 -> --model-endpoint on the host side, itself an SSH tunnel to our GPU instance opened by hand), because the
host firewall does not accept connections from the VM and is left as it is. The script ends only the tunnel process it started.

  python3 q3_open_weight_host.py --snapshot q4-base-20261023b --clients qwen,goose --signals none,list_changed \
      --out ../../results/q3/open-weight-20261025 --seed 20261025
"""
import argparse
import fcntl
import hashlib
import subprocess
import json
import random
import sys
import time
from pathlib import Path

import q3_host as qh

STATE = Path.home() / ".agentlab-q4"  # shared with the Q4 orchestrators: one campaign at a time drives the VM

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--clients", required=True)
    ap.add_argument("--signals", default="none,list_changed")
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--model-endpoint", default="192.168.122.1:11434", help="host:port of the model, as seen from the host")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out).resolve()
    # the same guards as q4_events.py / q4_baseline.py, before any campaign state is read or written or the VM is touched
    STATE.mkdir(exist_ok=True)
    lock = open(STATE / "vm.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("another campaign is driving the VM (vm.lock): not starting")
    if (STATE / "pending.json").exists():
        sys.exit("a Q4 campaign has evidence pending in the VM (pending.json): rerun it first; nothing restored")
    cells = [{"client": c, "signal": s} for c in a.clients.split(",") for s in a.signals.split(",")]
    random.Random(a.seed).shuffle(cells)
    here = Path(__file__).resolve().parent
    plan = {"seed": a.seed, "snapshot": a.snapshot, "model_endpoint": a.model_endpoint, "rep": a.rep, "cells": cells,
            "inputs_sha256": {f: hashlib.sha256((here / f).read_bytes()).hexdigest()
                              for f in ("q3-clients-open-weight.json", "record_proxy.py", "run_q3_cell.py")}}
    pf = out / "plan.json"
    if pf.exists():   # a resume must be the same campaign: same arguments and inputs, or nothing is done
        old = json.loads(pf.read_text())
        if {k: old.get(k) for k in plan} != plan:
            sys.exit(f"{pf} was made with other arguments or inputs; refusing to resume into this folder")
    else:
        out.mkdir(parents=True, exist_ok=True)
        pf.write_text(json.dumps({"made": qh.now(), **plan}, indent=2))
    h = qh.Host(out)
    try:
        run_cells(a, h, out, cells)
    finally:   # the VM is stopped whatever happened; the secret gate (open_weight_gate.py) runs before anything is published
        qh.run(qh.VIRSH + ["shutdown", qh.DOMAIN])


def run_cells(a, h, out, cells):
    for i, c in enumerate(cells, 1):
        name = f"{c['client']}__{c['signal']}__description__r{a.rep}"
        if (out / name / "row.json").exists():
            continue
        h.mem_ok()
        t0 = time.time()
        ip, boot_s = h.fresh_vm(a.snapshot)
        rc, hashes = h.push_harness(ip)
        h.note(event="cell-start", n=i, of=len(cells), cell=name, ip=ip, boot_s=boot_s, push_rc=rc)
        tunnel, result = None, None
        try:   # the tunnel this script owns is always ended and the cell's files always fetched, even when a step fails
            tunnel = subprocess.Popen(["ssh", "-N", "-i", str(qh.KEY), "-o", f"UserKnownHostsFile={qh.KNOWN}", "-o",
                                       "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
                                       "-R", f"127.0.0.1:11434:{a.model_endpoint}", f"lab@{ip}"])
            time.sleep(3)
            if tunnel.poll() is not None:
                raise RuntimeError(f"reverse tunnel ended at once (exit {tunnel.returncode})")
            up = h.ssh(ip, f"mkdir -p ~/q3-out/{name} && (nohup python3 ~/lab/tool-change/scripts/record_proxy.py "
                           f"--listen 127.0.0.1:11435 --upstream http://127.0.0.1:11434 --out ~/q3-out/{name}/proxy.jsonl "
                           f"--client {c['client']} > ~/q3-out/{name}/proxy.stderr.txt 2>&1 &) ; sleep 1; "
                           f"curl -sf -m 20 http://127.0.0.1:11435/api/version", timeout=60)
            if up.returncode != 0:
                raise RuntimeError("model endpoint not reachable through the proxy")
            result = h.ssh(ip, f"python3 ~/lab/tool-change/scripts/run_q3_cell.py --client {c['client']} --signal {c['signal']} "
                               f"--change description --rep {a.rep} --out ~/q3-out --clients-json "
                               f"/home/lab/lab/tool-change/scripts/q3-clients-open-weight.json", timeout=2400)
            (out / f"{name}.stdout.txt").write_text(result.stdout + "\n--- stderr ---\n" + result.stderr)
        except Exception as e:
            h.note(event="cell-failed", cell=name, error=type(e).__name__ + ": " + str(e)[:300])
        finally:
            if tunnel is not None:
                tunnel.terminate()
                try:
                    tunnel.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    tunnel.kill()
            h.fetch(ip, name, out / name, int(t0) - 5)
            (out / name / "harness-sha256.txt").write_text(hashes)
        h.note(event="cell-end", cell=name, rc=None if result is None else result.returncode,
               seconds=round(time.time() - t0), row=(out / name / "row.json").exists())
    h.note(event="matrix-done", cells=len(cells))


if __name__ == "__main__":
    main()
