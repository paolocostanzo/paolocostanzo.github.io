# Package for the second rule author (D27 point 7)

You write detection rules on the **default logs of AI coding clients**, using only the material in this folder. Read nothing
outside this folder. Do not search the disk, do not open parent folders, do not open any other file of the computer: a read
outside this folder invalidates your work.

## The clients
Five terminal AI coding clients: `claude-code`, `codex`, `gemini`, `qwen`, `goose`. Each archive holds the client's default log
locations after a run or a session, with secrets and personal data redacted.

## The three event classes
A client is connected to tool servers (MCP servers). Three things can happen:
- **q3-announced** — a server the user had already approved changes the definition of one of its tools (for example its
  description) while the client is running, and sends the standard "the tool list changed" notification.
- **q3-silent** — the same change of definition, without any notification.
- **q5-start** — when the client opens a project folder, a tool server declared by that project's own configuration files is
  started. The client may or may not start it; `event_happened` tells you whether it did.

A rule should fire when the event happens and not during normal use. Normal use here is an hour of scripted work: explain a file,
run the tests, edit a file, call one tool of an already approved server.

## The material
- `material/runs/<name>/`: one run per client and event class (and a few repeats). `meta.json` gives the client, the event class,
  the run's bounds `t0`/`t1` (Unix seconds), whether the event happened and its time (`event_time`, taken from ground truth
  outside the client's logs). `client-logs.redacted.tgz` is the client's logs.
- `material/sessions/<name>/`: hour-long sessions of normal use with no event. `meta.json` gives the client and `t_start`/`t_end`.
- `tools/normalize.py` reads an archive into time-ordered records; `tools/apply.py` applies a rule file. **The rule format is in
  the docstring at the top of `tools/apply.py`**; read it first. Example:
  `python3 -I tools/apply.py my-rules.json material/runs/X/client-logs.redacted.tgz codex START END`.

## What to write
One rule per (client, event class): 15 rules, ids `"<client>/<event>"`, in `rules-second-author.json` inside this folder, with
the format of `tools/apply.py` (fields `client`, `event`, `class` = a short description of what the rule recognises, `type`, and
the type's fields). If no record in a client's default logs can tell an event apart, write type `"none"` with a `"reason"`.

Aim: for each pair, the rule fires at or after `event_time` (within the run, until `t1`) in every run where the event happened,
and in fewer than 5% of the one-minute windows of the normal-use sessions of that client.

## Forbidden literals (D27 point 6)
A rule must be something a defender could write without knowing this lab. Never put in a rule any name that exists only because
of the lab's design: the lab's server and tool names, its folders, its repository names, its log file names, its network
addresses, or the text of the lab tool's descriptions. `tools/apply.py` refuses some of them (`FORBIDDEN`), but the rule is
yours to respect in full: patterns must describe a kind of record (for example "a tool's definition changed"), not this lab's
names.

## Deliverables (inside this folder)
- `rules-second-author.json`;
- `NOTES-second-author.md`: for each rule, what it looks at, why, and its results on the runs (fired or not, delay after the event)
  and on the sessions (false-positive windows); and every rule you tried and dropped, with the reason.
