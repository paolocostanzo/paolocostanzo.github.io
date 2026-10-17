#!/usr/bin/env python3
"""Host-side orchestration of the Q3 primary matrix (deviations D5, D12, D16).

For every repetition: restore the clean snapshot, start the VM, copy the harness in, run one cell
(run_q3_cell.py), copy the results and the raw telemetry out (audit log and journal, unfiltered),
stop the VM. Cells are run in a random order within each repetition block; the seed and the full
plan are written before the first cell, and a run can be resumed (cells with a row.json are skipped).

  python3 q3_host.py --snapshot q3-base-20261016 --reps 5 --seed 20261016 --out ../../results/q3/primary
"""
import argparse, datetime as dt, json, os, random, subprocess, sys, time
from pathlib import Path

DOMAIN = "agentlab"
VIRSH = ["virsh", "-c", "qemu:///system"]
KEY = Path.home() / ".ssh/agentlab_ed25519"
KNOWN = Path.home() / "Desktop/redazione/lab/vm/known_hosts"
HARNESS = Path(__file__).resolve().parent.parent  # tool-change/
FALLBACK_IP = "192.168.122.70"  # agentlab's address on libvirt's default NAT network (lab/vm, RIPRENDI §4)
CLIENTS = ["claude-code", "codex", "gemini", "qwen", "cursor-agent", "goose"]
SIGNALS = ["none", "list_changed"]

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

def run(cmd, timeout=300, check=False, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check, **kw)

class Host:
    def __init__(self, out):
        self.out = out
        self.log = out / "host-log.jsonl"

    def note(self, **kw):
        line = json.dumps({"ts": now(), **kw})
        with open(self.log, "a") as f:
            f.write(line + "\n")
        print(line, flush=True)

    def ip(self):
        # libvirt's DHCP lease table can expire while the guest, restored from a running snapshot, still holds its lease and
        # never renews it (15 Oct 2026, after ~1 h): fall back to the VM's known address.
        r = run(VIRSH + ["domifaddr", DOMAIN])
        for line in r.stdout.splitlines():
            if "ipv4" in line:
                return line.split()[-1].split("/")[0]
        return FALLBACK_IP

    def ssh(self, ip, command, timeout=120):
        return run(["ssh", "-i", str(KEY), "-o", f"UserKnownHostsFile={KNOWN}", "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=5", f"lab@{ip}", command], timeout=timeout)

    def mem_ok(self, need_mb=600, wait_s=600):
        t0 = time.time()
        while True:
            avail = int(next(l for l in open("/proc/meminfo") if l.startswith("MemAvailable")).split()[1]) // 1024
            if avail >= need_mb:
                return avail
            if time.time() - t0 > wait_s:
                self.note(event="abort-low-memory", avail_mb=avail)
                sys.exit(3)
            time.sleep(20)

    def order6_blocks(self):
        """Free blocks of order >= 6 in zone Normal: libvirt needs one to load the VM's AppArmor profile
        (15 Oct 2026: 'apparmor_parser: page allocation failure: order:6' when there were none)."""
        for line in open("/proc/buddyinfo"):
            if " Normal " in line:
                return sum(int(x) for x in line.split()[10:])
        return -1

    def fresh_vm(self, snapshot):
        """Restore the clean snapshot. A running snapshot (memory included) restores in seconds; libvirt still starts a
        new QEMU process, so the AppArmor profile is loaded again: on failure, wait and retry, then stop cleanly."""
        for attempt in range(1, 6):
            r = run(VIRSH + ["snapshot-revert", DOMAIN, snapshot])
            if r.returncode == 0 and "running" not in run(VIRSH + ["domstate", DOMAIN]).stdout and \
                    "esecuzione" not in run(VIRSH + ["domstate", DOMAIN]).stdout:
                r = run(VIRSH + ["start", DOMAIN])
            if r.returncode == 0:
                break
            self.note(event="revert-failed", attempt=attempt, stderr=r.stderr.strip()[:300], order6plus=self.order6_blocks())
            time.sleep(60)
        else:
            self.note(event="abort-revert", hint="host memory fragmented: free RAM or reboot, then resume (done cells are skipped)")
            sys.exit(4)
        t0 = time.time()
        while time.time() - t0 < 240:
            ip = self.ip()
            if ip and self.ssh(ip, "true", timeout=15).returncode == 0:
                # the guest clock comes back with the snapshot: set it to the host's time
                self.ssh(ip, f"sudo date -s @{int(time.time())} >/dev/null")
                return ip, round(time.time() - t0)
            time.sleep(3)
        self.note(event="ssh-timeout")
        sys.exit(6)

    def push_harness(self, ip):
        p1 = subprocess.Popen(["tar", "-C", str(HARNESS.parent), "-czf", "-", HARNESS.name], stdout=subprocess.PIPE)
        p2 = subprocess.run(["ssh", "-i", str(KEY), "-o", f"UserKnownHostsFile={KNOWN}", "-o", "BatchMode=yes", f"lab@{ip}",
                             "mkdir -p ~/lab && rm -rf ~/lab/tool-change && tar -C ~/lab -xzf -"], stdin=p1.stdout,
                            capture_output=True, timeout=120)
        p1.wait()
        h = self.ssh(ip, "cd ~/lab/tool-change && find . -type f -not -path './test/*' | sort | xargs sha256sum")
        return p2.returncode, h.stdout

    def fetch(self, ip, remote_dir, local_dir, since_epoch):
        local_dir.mkdir(parents=True, exist_ok=True)
        cmd = f"tar -C ~/q3-out -czf - {remote_dir}"
        with open(local_dir.parent / f"{remote_dir}.tgz", "wb") as f:
            subprocess.run(["ssh", "-i", str(KEY), "-o", f"UserKnownHostsFile={KNOWN}", "-o", "BatchMode=yes", f"lab@{ip}", cmd],
                           stdout=f, timeout=120)
        run(["tar", "-C", str(local_dir.parent), "-xzf", str(local_dir.parent / f"{remote_dir}.tgz")])
        tel = local_dir / "telemetry"
        tel.mkdir(exist_ok=True)
        for name, rcmd in [("audit.log", "sync; sleep 1; sudo cat /var/log/audit/audit.log"),
                           ("journal.txt", f"sudo journalctl -o short-iso-precise --since @{since_epoch} --no-pager"),
                           ("nft-ruleset.txt", "sudo nft list ruleset"),
                           ("auditctl-rules.txt", "sudo auditctl -l")]:
            r = self.ssh(ip, rcmd, timeout=120)
            (tel / name).write_text(r.stdout)

def plan(reps, seed, clients, signals):
    rng = random.Random(seed)
    cells = []
    for rep in range(1, reps + 1):
        block = [(c, s) for c in clients for s in signals]
        rng.shuffle(block)
        cells += [{"rep": rep, "client": c, "signal": s} for c, s in block]
    return cells

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--clients", default=",".join(CLIENTS))
    ap.add_argument("--signals", default=",".join(SIGNALS))
    ap.add_argument("--change", default="description")
    ap.add_argument("--out", required=True)
    ap.add_argument("--extra-args", default="", help="passed to run_q3_cell.py")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    h = Host(out)
    cells = plan(a.reps, a.seed, a.clients.split(","), a.signals.split(","))
    pf = out / "plan.json"
    if not pf.exists():
        pf.write_text(json.dumps({"created": now(), "snapshot": a.snapshot, "seed": a.seed, "change": a.change,
                                  "cells": cells}, indent=2))
    for i, c in enumerate(cells, 1):
        name = f"{c['client']}__{c['signal']}__{a.change}__r{c['rep']}"
        if (out / name / "row.json").exists():
            continue
        avail = h.mem_ok()
        t0 = time.time()
        h.note(event="pre-revert", cell=name, order6plus=h.order6_blocks())
        ip, boot_s = h.fresh_vm(a.snapshot)
        rc, hashes = h.push_harness(ip)
        h.note(event="cell-start", n=i, of=len(cells), cell=name, ip=ip, boot_s=boot_s, host_avail_mb=avail, push_rc=rc)
        r = h.ssh(ip, f"python3 ~/lab/tool-change/scripts/run_q3_cell.py --client {c['client']} --signal {c['signal']} "
                      f"--change {a.change} --rep {c['rep']} --out ~/q3-out {a.extra_args}", timeout=1500)
        (out / f"{name}.stdout.txt").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
        h.fetch(ip, name, out / name, int(t0) - 5)
        (out / name / "harness-sha256.txt").write_text(hashes)
        h.note(event="cell-end", cell=name, rc=r.returncode, seconds=round(time.time() - t0),
               row=(out / name / "row.json").exists())
    h.note(event="matrix-done", cells=len(cells))
    run(VIRSH + ["shutdown", DOMAIN])

if __name__ == "__main__":
    main()
