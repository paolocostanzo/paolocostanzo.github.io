# Open-weight cells (D39, exploratory), 24 October 2026

Part 2 announced that the clients that accept another model endpoint would also run against an open-weight model on a GPU we
control, to see which definition of a changed tool each client hands to the model. Deviation D39 (`preregistration/DEVIATIONS.md`,
points 1–6, published before any cell) fixes how.

- Model: `qwen3.8:27b-q8_0` (digest `8f5fb6b71ea00052cbe8545738c55ce61112c4e571cb60ca4dad00b131766039`), Ollama 0.40.2, on our own
  GPU instance (g6e.xlarge, NVIDIA L40S), reached from the disposable VM through SSH tunnels.
- Clients (VM snapshot of part 4): Qwen Code 0.25.1, Goose 1.54.0, Codex CLI 0.162.1, Claude Code 2.1.300, each pointed at the
  model through `scripts/record_proxy.py` with `scripts/q3-clients-open-weight.json`. Gemini CLI and Cursor CLI have no documented
  option for another endpoint.
- Cells: 4 clients × signal {none, `notifications/tools/list_changed`} × 1 repetition, description change only, the three phases
  of D16 (call; change A→B; call; reopen and call). Order and seed in `plan.json`; host events in `host-log.jsonl`.
- Run by `scripts/q3_open_weight_host.py`; analysed by `scripts/analyze_open_weight.py` → `RESULTS.json`. The cells ran with the
  orchestrator of commit 4040c9f. `record_proxy.py` was that of 4040c9f in the first cell and that of f22573f in cells 2–8; the
  only change forwards and marks malformed tool metadata, and no cell had an inspection failure. Each cell's
  `harness-sha256.txt` records the exact files.
- `model.json`: the models on the instance as Ollama listed them (`/api/tags`) at the start of the session, with their digests.
- Transport: Qwen Code closed some streamed requests early, and one upstream request ended without a response; the phase counts
  are unaffected (`client_hung_up`, `endpoint_errors` in `RESULTS.json`).

Per cell: `row.json` (the runner's record, with the phase times), `q3-server.log` (the lab server: notifications, `tools/list`,
calls), `q3-control.json`, `proxy.jsonl` (one `request` line per request with the lab tool's entry and revision, `tools_total`, and
revision counts found elsewhere in the request; one `done` line with the status), `harness-sha256.txt`, and the runner's stdout.

Kept out of this public copy, as for part 4's evidence: the screen captures of the clients, the raw host telemetry, and the other
tool entries of each request (in Codex CLI they describe the apps connected to the VM's Codex account; only their number is kept).
The secret gate of D26 (`scripts/open_weight_gate.py`) scanned this folder: `secret-gate.json`, bound to the SHA-256 of every file.

**Results (exploratory; the proxy shows what each client sent, not what the model used).** In none of the four clients did a
request to the model carry the new description before the client was reopened, with or without the notification. Qwen Code and
Goose: the model called the tool in every phase. Codex CLI: the model never called the tool; why was not established. Claude Code:
the endpoint rejected every main request (HTTP 500), so its model never answered; a new turn started after the change still carried
the old description, also after Claude Code had fetched the new list from the server (one run, after repeated errors). One
repetition per cell, one model, one endpoint.
