# Notes on the two matrix runs (9 October 2026)

Both runs started from the same VM snapshot (`telemetry-20261009`: clients logged in, telemetry on).

- **20261009-194314** — full matrix, 2 repetitions, 6 clients.
  - **Codex rows are invalid**: from the second Codex cell on, Codex showed "Cannot use the background server —
    Experimental feature request failed" with *Cancel* pre-selected, so the trust prompt was never reached.
    Its background component had updated itself to 0.162.0 during the run (CLI still 0.157.1). These rows are
    excluded from `../q5-summary.md`.
  - **Cursor CLI updated itself** from 2026.09.26-dd393fe to 2026.10.01-e373342 during its first cell
    (see `run.json`, `versions_after`). Its results match the manual run of 28 September on 2026.09.26.
  - Gemini `after-decision` r1: canary not observed within 15 s of trusting the folder (no later probe in this run).
- **20261009-200610** — targeted rerun after reverting the snapshot, 3 repetitions: Codex (all cells) and Gemini
  `after-decision` with an extra probe 25 s later. The runner answers the Codex daemon screen with
  "Run without daemon this time" and records the client version before every cell (`version_cell`).

`../q5-summary.md` combines the valid rows of both runs.
