# Frozen rules and scoring tools for RQ5 (D27 point 5, D36)

Frozen on 24 ott 2026 08:34 CEST, before any holdout session is opened for scoring (see `holdout-access.md`).
Verify with `sha256sum -c telemetry/rules/FROZEN-SHA256.txt` from the repository root.

- Rules: `rules-author.json` (the author's, H5 is judged on these), `rules-second-author.json` (blind second author, D27 point 7:
  agreement only; package, prompt and notes in `second-author/`; its helper rule `claude-code/q3-silent--changed` only feeds
  `claude-code/q3-silent`), `host-known-commands.json` (learning set of the host rule, D36).
- Tools: `normalize.py` (with `../scripts/vm_logs.py` and `../inventory/candidates.json`), `apply.py`, `host.py`,
  `scores_input.py`, `h5.py`, `../scripts/score.py` (window definition: one-minute windows from t_start, D27 point 3).
- Tool versions: Python 3.13.5 with SQLite 3.46.1 (normalize.py reads Codex CLI's and Goose's databases), on Linux
  7.1.13+parrot7-amd64.
- Inputs: the redacted archives made by the harness at the campaign commits (events: the commit in `events/plan.json`; baseline:
  `acq-baseline` d50fd32, D37), with the secret gate of D26; their SHA-256 are in each run's `collection.json` and harness hash
  files, written by the runner.
- Learning set of the host rule: built from the development and set-up sessions (`setup-r1` to `setup-r7`, the development parts
  of `baseline-r2`, `-r3` and `-r4`), inside their windows. It holds the lab's own server and paths as data: it keeps the lab
  server's launch in the holdouts from counting as new. Without that entry the host rule would fire about once per session.
  `host.py` runs no literal check: the learned set is data, not rule text (D36).
- Second author's session: package SHA256SUMS sha256 2cde2127432ccf17cb588840f93bf7bf37a47665d12a987150ed84b67097d30b; prompt
  (as run, with the package's absolute path) sha256 4791b2552627c1779c2dac82a4bc55bd256a394719600f3d4046f83fb16fbd6b; model: the
  coordinator's (Claude Opus 5.5), a fresh subagent session with no conversation history (Claude Code 2.1.304, parent session
  fd8b0aa0-9132-4601-a6b0-a5cbaa27c4f6, agent aa4bcbb23ecfb8d9a), 06:20–06:32 UTC on 24 Oct; its context also received, from the
  harness and not by reading, the user's instruction files (CLAUDE.md, MEMORY.md), which hold no lab design but do hold
  confidential notes: the raw transcript is never published (a redacted copy, if any, gets its own hash); transcript kept by the author
  (sha256 00914e5607798035f34a4346a5aac2ea9bd060dac269c9c780df513b777c08b4), checked: 46 tool
  calls, no read outside the package. Packaging fault of the author: `normalize.py` needs `vm_logs.py` and `candidates.json`,
  which were not in the package; the second author rebuilt time parsing inside the package to test its rules (an import of the
  missing file, inside the package, failed and read nothing); it took the client log locations from each archive's manifest and
wrote its own time parser for its tests. Its rules were re-run with the real tools on the same material: same results
(`second-author/VALUTAZIONE-SECONDO-AUTORE.txt`). The README told it that hits count until t1; scoring counts until t1 + 120 s
(D36), which can only add hits. Its helper rule appears in the H5 output as "not measurable" and is left out of the agreement.
- Rule type "unannounced" (used only by the second author's `claude-code/q3-silent`): as `apply.py` documents it, a change is
  suppressed by any announcement hit of the named rule between the two observations, whatever the server or project of either
  (Codex review of the freeze, FREEZE-01). The tooling is not changed after the second author wrote against it. In this lab one
  server sends notifications, so the case does not arise; elsewhere the rule would miss a silent change that happens while another
  server announces one. Reported with the agreement results.
- `scores_input.py` lists absent or unusable evidence of a planned run or holdout under "missing" and marks its client incomplete
  instead of failing (FREEZE-02): an archive that does not open; a ground-truth log absent or unparsable; client-log evidence whose
  runner's collection check (`collection.json`, made on the original archive before redaction) is not "ok" for the client, or whose
  redacted archive lacks a manifest with an explicit entry for the client or a file that entry lists. An explicit entry listing no
  file (no default log) is a real observation.
