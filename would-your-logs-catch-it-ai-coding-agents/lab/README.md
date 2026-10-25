# Lab for part 4 of Outside the Prompt: what a defender can see

Copy of the folders of the lab repository that part 4 uses, at release v0.4.2 (https://doi.org/10.5281/zenodo.23623381, commit 7709f31). The evidence of every
run and session (`telemetry/evidence/`) is in the release, not in this copy:

| Folder | What it is |
|---|---|
| `telemetry/` | Q4: inventory of default client log locations, exporter run inside the VM, collection check, event and baseline orchestrators, scoring (`README.md` there) |
| `defence-coverage/` | Q2: second reading, comparison of the two readings, final census and coverage tables |
| `tool-change/` | the lab server, the runner and the helpers that `telemetry/` and `defence-coverage/` import, and the open-weight cells of D39 with their evidence (`evidence/open-weight-20261024/`); the part 3 evidence is with part 3 |
| `trusting-a-repo/` | only the Q5 runner and the canary server that the event runs use |

**This copy is for reading and for the tests, not for running the measurements.** The event and baseline orchestrators
(`telemetry/scripts/q4_events.py`, `q4_baseline.py`) record the lab's git commit in their plan and refuse to start without it:
run them from a checkout of the lab repository at the release commit (v0.4.2, 7709f31), not from this folder.

Where each part runs:
- on the host: the orchestrators, which restore the VM snapshot, copy the harness in, and check what comes back;
- inside the disposable lab VM only: everything that starts, stops or resets clients (`stop_clients.sh` refuses to run anywhere
  else), and `vm_logs.py collect`;
- anywhere: the read-only check of section [06] of the post (`telemetry/scripts/vm_logs.py inventory`), the scoring and the
  census scripts.

Tests, from this folder: `python3 -m unittest discover -s telemetry/test` (221), `python3 -m unittest discover -s defence-coverage/test` (28) and `python3 -m unittest discover -s tool-change/test -p 'test_*.py'` (23).
