#!/usr/bin/env python3
"""Runs INSIDE the disposable VM: one baseline session of scripted normal use for one terminal client (D13, D24 point 4).

Cycles of the fixed tasks in ../baseline/tasks.json until --minutes are over: open the client in the trusted test folder
~/q3-repo, explain a file, run the tests, edit a file, call the known q3-lab tool (definition A, never changed), close the client.
Permission questions for commands and edits are answered "allow once" (../baseline/clients.json); the MCP tool approval and the
client's ready pattern are the frozen Q3 ones (tool-change/scripts/q3-clients.json). Every action is written with its time to
timeline.jsonl, which the analysis uses to place the one-minute windows; every screen at the end of a task is kept.

  python3 baseline_session.py --client codex --part dev --minutes 60 --out ~/q4-out
  python3 baseline_session.py --client codex --part setup --minutes 6 --out ~/q4-out    # exploratory set-up session

The q3-lab server is set to definition A with no signal before the first cycle, and checked again at the end: a session in which
the server log shows any definition other than A is invalid for the baseline. A session is also invalid unless the normal use
actually happened (tasks.json "workload_success": share of tasks completed, known-server calls per cycle). session.json records
both checks; the host keeps an invalid session as an attempt and does not count it. The local-engine repetition (D13 point 1)
is not implemented here: there is no part for it, and it is reported as not done unless a later, reviewed change adds it.
"""
import argparse, difflib, importlib.util, json, re, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEL = HERE.parent
_spec = importlib.util.spec_from_file_location("run_q3_cell", TEL.parent / "tool-change/scripts/run_q3_cell.py")
q3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(q3)
_qspec = importlib.util.spec_from_file_location("quota", HERE / "quota.py")
quota = importlib.util.module_from_spec(_qspec)
_qspec.loader.exec_module(quota)
# The lab's known tool. Only a question that names it gets the frozen Q3 MCP approval; every other question (commands, edits)
# gets "allow once". For Claude Code both questions read "Do you want to proceed?", and the Q3 keys give "don't ask again".
LAB_TOOL = re.compile(r"q3_noop|q3-lab")
QUESTION_LINES = 25  # the bottom of the screen, where the clients draw their questions
DIALOG_LINES = 10    # the active dialog: the lines just above the question line
# Headings of command and edit dialogs: when one is in the active dialog, the question is never an MCP approval, even if the lab
# tool's name appears (recent tool output, or a command that mentions it).
# A persistent approval needs a positive sign that the active dialog is an MCP tool dialog; anything ambiguous gets "allow once".
MCP_DIALOG = re.compile(r"\(MCP\)|MCP server|MCP tool|mcp__", re.I)
# A reply that is a model or API error, not an answer (set-up of 22 Oct 2026: Goose "Ran into this error: Server error (503 …)",
# Gemini CLI "The model returned an empty response"). The client returns to its ready screen anyway.
ANSWER_ERROR = re.compile(r"Ran into this error|Server error \(\d{3}|\b5\d\d Service Unavailable|experiencing high demand|"
                          r"\[API Error|API Error:|Request failed with status|returned an empty response|This request failed|"
                          r"stream error|stream disconnected|\bOverloaded\b", re.I)
# Advice that clients print with an error ("Please retry if you think this is a transient or recoverable error.", Goose 22 Oct):
# part of the error block, never answer text (Codex review of D28, round 2, G2-01).
ERROR_TAIL = re.compile(r"Please retry|transient or recoverable|try again later|Retrying", re.I)
# Screen furniture, not part of an answer: footers, input boxes, progress bars, elapsed-time lines, lines of symbols only.
CHROME = re.compile(r"~/q3-repo|/home/lab|for shortcuts|auto mode|for agents|Type your message|Add a follow-up|⏱|╌|context left|"
                    r"tokens|esc to|ctrl\+|shift\+tab|^workspace \(|Press a number|GPT-|\bOpus\b|default ·|goose is ready|"
                    r"^[\s│─━╭╮╰╯┌┐└┘═▔▁>›❯→•●✻⎿▸◐◓◑◒⠀-⣿.]*$", re.I)
NOISE_LINES = 12   # an unrelated first-run or account notice is answered only while it is drawn at the bottom of the screen
# A client that is still working (set-up of 22 Oct 2026): Goose keeps "goose is ready" on screen while it works, and Gemini CLI and
# Qwen Code draw "Type your message" under their spinner, so the ready pattern alone does not mean the reply is over. Goose also
# leaves old spinner lines in its transcript: a working line counts only when no end-of-turn line is drawn after it.
BUSY = re.compile(r"Ctrl\+C to interrupt|esc to (?:cancel|interrupt)|ctrl\+c to stop", re.I)
TURN_END = re.compile(r"⏱ ?\d|\bfor \d+(?:\.\d+)?s\b|Worked for|· done \d", re.I)
BUSY_LINES = 15
NOT_SUBMITTED_S = 60  # a prompt after whose Enter nothing changes on screen for this long, with no sign of work, was not submitted
MAX_QUESTIONS = 3     # unexpected questions answered per task; more means the driver is misreading the screen
SETTLE_S = 90         # longest wait for a ready, idle client before a prompt; past it the cycle ends (no typing into a busy client)
HARD_FAILURES = ("not-settled", "prompt-not-submitted", "question-limit", "timeout", "prompt-mangled")  # the cycle ends after one
FOLD = re.compile(r"[\s│┃█▐▌]")  # white space, box borders and scroll bars: what a wrapped input line adds between characters
MARKERS = "[>›❯→*]*"  # the input markers that may stand before a prompt on its first line
# Qwen Code draws its cursor as U+200B right after the typed text (setup-r6, D35): zero-width characters are allowed only at the
# very end of the input, never inside it, not even at a line end inside a wrapped prompt (Codex review of D35, D35-01)
CURSOR = "[\u200b-\u200d\u2060\ufeff]*"
TYPED_READS = 5  # reads of the input line, 0.6 s apart, while it still changes (a slow client may draw it in steps, D35)


def fold(line):
    return FOLD.sub("", line)


def composer_holds(before, typed, prompt):
    """True when the lines that typing drew or changed hold the whole prompt, unaltered. The screen before typing is compared
    line by line with the screen after, so that an earlier cycle's echo of the same prompt does not count; white space and box
    borders are dropped, so that a wrapped input line reads as one (D34: Qwen Code's survey swallowed the digits of a prompt)."""
    a, b = before.rstrip().splitlines(), typed.rstrip().splitlines()
    new = [l for op, _, _, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != "equal"
           for l in b[j1:j2]]
    # the input is the prompt alone: it may run over line ends, only an input marker may stand before it on its first line, and
    # nothing after it on its last, so a survey's "0" left before or after it fails (Codex review of D34, D34-01)
    text = "\n".join(fold(l) for l in new)
    body = "\n?".join(map(re.escape, fold(prompt)))
    return re.search(r"(?:^|\n)" + MARKERS + body + CURSOR + r"(?=\n|$)", text) is not None


def type_literal(text):
    """The prompt's text, without Enter (tmux send-keys -l); Enter is sent separately, after the screen is read."""
    q3.sh(f"tmux send-keys -t {q3.SESSION} -l {json.dumps(text)}")


def turn_outcome(screen, prompt):
    """The current turn's outcome from the screen, after the prompt's echo: "error" when a model or API error is drawn with no
    answer after it, "answered" when answer text is drawn (after the last error, if any), "empty" otherwise. Lines that are
    screen furniture, or part of the echoed prompt, are not answer text."""
    anchor = prompt[:30]
    reply = screen[screen.rfind(anchor):] if anchor and anchor in screen else screen
    lines = reply.rstrip().splitlines()[1:] if anchor and anchor in screen else reply.rstrip().splitlines()
    def answer_text(line):
        t = line.strip()
        return (len(re.findall(r"[A-Za-z]{2,}", t)) >= 3 and not CHROME.search(t) and not ANSWER_ERROR.search(t)
                and not ERROR_TAIL.search(t) and t.strip("›>❯● ") not in prompt)
    errors = [i for i, l in enumerate(lines) if ANSWER_ERROR.search(l)]
    after = lines[errors[-1] + 1:] if errors else lines
    if any(answer_text(l) for l in after):
        return "answered"
    return "error" if errors else "empty"


COMMAND_DIALOG = re.compile(r"Bash command|Edit file|Create file|Write file|Run this command|Would you like to run the following command|"
                            r"Would you like to make the following edits|Apply this change|shell command", re.I)


class Session:
    def __init__(self, client, part, outdir, tasks, perms, cell=None):
        self.client, self.part, self.out = client, part, outdir
        self.cell = cell or f"baseline-{client}-{part}"
        self.spec = q3.CLIENTS[client]
        self.tasks, self.perm = tasks, perms.get(client, {})
        # the ready pattern for the baseline: clients.json "ready" if given (D29: Goose's composer once its banner has scrolled
        # away), otherwise the frozen Q3 one
        self.ready = self.perm.get("ready") or self.spec["ready"]
        # a line the client prints when a turn is over (clients.json "turn_end", D30: Goose's "⏱ Ns"); when given, a turn is over
        # only once such a line is drawn after the prompt's echo, whatever the screen looks like in between
        self.turn_end = re.compile(self.perm["turn_end"]) if self.perm.get("turn_end") else None
        self.last_prompt = None
        self.generic = perms.get("_generic")
        self.timeline = open(outdir / "timeline.jsonl", "a")
        self.n_caps = 0
        self.quota_message = None
        self.server_prompts = 0
        self.error_replies = 0   # tasks whose turn ended on a model or API error (D28: provider unavailable)
        self.fail = None  # cycles that reached the known-server task (D28 proposal: the per-cycle denominator)

    def log(self, **kw):
        self.timeline.write(json.dumps({"ts": q3.now(), "epoch": round(time.time(), 3), **kw}) + "\n")
        self.timeline.flush()

    def capture(self, label):
        self.n_caps += 1
        name = f"{self.client}__{self.part}__{self.n_caps:04d}-{label}.txt"
        (self.out / "screens" / name).write_text(q3.screen())
        return name

    def answer_questions(self, s, cycle, task, answer_unexpected=True):
        """Answer a permission question on screen, if any. Returns the kind answered, or None.
        The frozen Q3 MCP approval is used only for a question that names the lab tool; command and edit questions are always
        answered "allow once", so that nothing is allowed permanently (D24 point 4)."""
        # tmux captures the whole pane: drop the blank lines below the last drawn line first (a 50-line pane can end with
        # 20+ empty lines, which would push the question out of the window)
        drawn = s.rstrip().splitlines()
        lines = drawn[-QUESTION_LINES:]
        bottom = "\n".join(lines)
        if self.spec.get("approve"):
            hits = [i for i, l in enumerate(lines) if re.search(self.spec["approve"], l)]
            header = self.perm.get("mcp_header")
            if hits and header:  # the active dialog = the lines just above the last question line, the question included
                window = lines[max(0, hits[-1] - DIALOG_LINES):hits[-1] + 1]
                # persistent approval only on positive identification: the client's exact MCP dialog line for the lab tool
                # (baseline/clients.json "mcp_header") inside the active dialog, and no command or edit heading at the bottom
                option = self.perm.get("mcp_option")
                options = lines[hits[-1] + 1:hits[-1] + 8]  # the choices drawn under the question
                if any(re.search(header, l) for l in window) and not COMMAND_DIALOG.search(bottom) \
                        and (option is None or any(re.search(option, l) for l in options)):
                    q3.keys(*(self.spec.get("approve_keys") or ("Enter",)))
                    return "mcp-approval"
        if self.perm.get("allow") and re.search(self.perm["allow"], bottom):
            q3.keys(*self.perm["allow_keys"])
            return "allow-once"
        if self.generic and re.search(self.generic, "\n".join(drawn[-12:])):  # bottom of the drawn screen only
            if not answer_unexpected:  # D28 point 3: the fourth unexpected question is not answered
                return "question-limit"
            q3.keys("Enter")
            return "unexpected-question"
        return None

    def quota_hit(self, s, cycle, task_id):
        """True (and the session ends) when the screen shows a quota or rate-limit message."""
        msg = quota.find(self.client, s)
        if msg and not self.quota_message:
            self.quota_message = msg
            self.log(action="quota", cycle=cycle, task=task_id, message=msg, screen=self.capture("quota"))
        return bool(msg)

    def server_calls(self):
        return sum(1 for e in q3.events() if e.get("event") == "tools/call" and e.get("cell") == self.cell)

    def evidence(self, task, cycle, t_prompt, calls_before, screen="", prompt=""):
        """Whether the action the task asked for really happened (tasks.json "evidence"); the ready screen alone is not enough.
        "answered": the client is back at its ready screen and the turn ended on answer text, not on an error (turn_outcome)."""
        kind = task.get("evidence", "answered")
        if kind == "answered":
            return turn_outcome(screen, prompt) == "answered"
        if kind == "test-run":
            f = q3.REPO / ".q4-test-runs"
            return f.exists() and any(float(x) >= t_prompt - 1 for x in f.read_text().split() if x.replace(".", "", 1).isdigit())
        if kind == "file-contains":
            f = q3.REPO / task["file"]
            return f.exists() and task["contains"].replace("{k}", str(cycle)) in f.read_text()
        if kind == "server-call":
            return self.server_calls() > calls_before
        raise ValueError(f"unknown evidence {kind}")

    def turn_over(self, s, prompt):
        """For a client with a turn-end line: True once that line is drawn after the prompt's echo (no prompt yet: True).
        In setup-r3 (22 Oct 2026) Goose, with a slow model, stayed still mid-turn between a tool's output and the model's reply,
        with no working line; a prompt typed meanwhile was drawn as a plain line, queued, and run after the current turn, with
        its echo ("> prompt") drawn only then. So the echo is the line in the client's echo format (clients.json "echo"), not a
        queued copy; an echo that is the last drawn line belongs to a turn that has just started."""
        if not self.turn_end or not prompt:
            return True
        lines = s.rstrip().splitlines()
        echo_re = re.compile((self.perm.get("echo") or "") + re.escape(prompt[:30]))
        echoes = [i for i, l in enumerate(lines) if echo_re.search(l)]
        if not echoes:
            if any(prompt[:30] in l for l in lines):
                return False  # only a queued copy: the prompt's turn has not started
            return any(self.turn_end.search(l) for l in lines)  # the echo has scrolled away: the turn has gone on since
        if echoes[-1] == len(lines) - 1:
            return False
        return any(self.turn_end.search(l) for l in lines[echoes[-1] + 1:])

    def busy(self, s):
        """A working line in the bottom lines with no end-of-turn line after it."""
        lines = s.rstrip().splitlines()[-BUSY_LINES:]
        busy = [i for i, l in enumerate(lines) if BUSY.search(l)]
        ends = [i for i, l in enumerate(lines) if TURN_END.search(l)]
        return bool(busy) and (not ends or busy[-1] > ends[-1])

    def notice_at_bottom(self, s):
        bottom = "\n".join(s.rstrip().splitlines()[-NOISE_LINES:])
        for tip, answer in list(q3.NOISE) + [tuple(n) for n in self.perm.get("notices") or []]:
            if re.search(tip, bottom):
                return answer
        return None

    def answer_notice(self, s, cycle, task_id):
        """Answer an unrelated notice (tool-change q3-clients.json "noise", unchanged) only while it is drawn at the bottom of the
        screen, and never while the client is working (Escape interrupts a turn in some clients). The screen is kept before the
        keys are sent. Returns True when a notice was answered."""
        if self.busy(s):
            return False
        # the frozen Q3 notices, then the baseline's own per client (clients.json "notices"; D34: Qwen Code's feedback survey,
        # whose digit keys would otherwise swallow the digits of the next prompt)
        answer = self.notice_at_bottom(s)
        if answer is None:
            return False
        self.log(action="noise", cycle=cycle, task=task_id, screen=self.capture("noise"))
        q3.keys(*answer)
        time.sleep(1)
        # the screen after the keys, kept with the one before: set-up checks that the notice is gone (adversarial review of D34)
        self.log(action="noise-after", cycle=cycle, task=task_id, screen=self.capture("noise-after"))
        return True

    def settle(self, cycle, task_id, idle_s, timeout=SETTLE_S):
        """Before typing a prompt: wait until the screen is ready, not busy and unchanged for idle_s (a notice can be drawn a few
        seconds after the ready marker, as Codex's account card was) and free of notices. False when the
        client never settled: the caller types nothing and ends the cycle."""
        t0, last, since, settled, answered = time.time(), None, time.time(), False, 0
        while time.time() - t0 < timeout:
            s = q3.screen()
            if s != last:
                last, since = s, time.time()
            elif time.time() - since >= idle_s and not self.busy(s) and re.search(self.ready, s) \
                    and self.turn_over(s, self.last_prompt):
                # a notice can be drawn after the ready marker (Qwen Code's survey came up to about 16 s after a turn): it is
                # answered, at most twice, and the client must then settle again; typing follows only a screen that is ready,
                # idle, past its turn and free of notices (adversarial and Codex reviews of D34)
                if self.notice_at_bottom(s) is None:
                    settled = True
                    break
                if answered == 2:
                    break
                self.answer_notice(s, cycle, task_id)
                answered, last = answered + 1, None
            time.sleep(1.5)
        return settled

    def end(self, action, cycle, task, label, screen, prompt, **kw):
        """Every task exit: one record with the turn's outcome on the last screen seen, counted once per task."""
        error = action != "quota" and turn_outcome(screen, prompt) == "error"  # a quota stop is not a provider failure (D27 point 8)
        self.error_replies += error
        self.fail = action if action in HARD_FAILURES else None
        self.log(action=action, cycle=cycle, task=task["id"], error_reply=error, screen=self.capture(label), **kw)

    def do_task(self, cycle, task, timeout, idle_s):
        """True when the client is back at its ready screen with a stable screen, no working line, a sign that the prompt was
        submitted, before the timeout, AND the task's evidence is there (a refused edit or a failed command returns to the ready
        screen too). Sets self.fail to a hard failure (HARD_FAILURES) when the cycle must end."""
        prompt = task["prompt"].replace("{k}", str(cycle))
        self.fail = None
        if not self.settle(cycle, task["id"], idle_s):
            s = q3.screen()
            self.end("not-settled", cycle, task, f"c{cycle}-{task['id']}-unsettled", s, prompt)
            return False
        t_prompt, calls_before = time.time(), self.server_calls()
        before = q3.screen()
        type_literal(prompt)
        self.last_prompt = prompt
        time.sleep(0.6)
        typed = q3.screen()  # the prompt in the composer, before Enter: anything else after Enter means it was submitted
        reads = 1
        while reads < TYPED_READS and not composer_holds(before, typed, prompt):  # read again while the line is still drawn
            time.sleep(0.6)
            again, reads = q3.screen(), reads + 1
            if again == typed:
                break
            typed = again
        if not composer_holds(before, typed, prompt):
            # an altered prompt is not the scripted use: no Enter, the cycle ends and the client is closed (D34)
            self.end("prompt-mangled", cycle, task, f"c{cycle}-{task['id']}-mangled", typed, prompt, reads=reads)
            return False
        q3.keys("Enter")
        self.log(action="prompt", cycle=cycle, task=task["id"], reads=reads)
        notices = questions = 0
        t0, last, stable_since, worked, s = time.time(), None, time.time(), False, typed
        while time.time() - t0 < timeout:
            time.sleep(1.5)
            s = q3.screen()
            worked = worked or s != typed or self.busy(s)
            if self.quota_hit(s, cycle, task["id"]):
                self.end("quota", cycle, task, f"c{cycle}-{task['id']}-quota", s, prompt)
                return False
            if not worked and time.time() - t0 >= NOT_SUBMITTED_S:
                self.end("prompt-not-submitted", cycle, task, f"c{cycle}-{task['id']}-unsent", s, prompt)
                return False
            if notices < 1 and self.answer_notice(s, cycle, task["id"]):
                notices += 1
                continue
            kind = self.answer_questions(s, cycle, task, answer_unexpected=questions < MAX_QUESTIONS)
            if kind == "question-limit":
                self.end("question-limit", cycle, task, f"c{cycle}-{task['id']}-questions", s, prompt)
                return False
            if kind:
                self.log(action=kind, cycle=cycle, task=task["id"], screen=self.capture(kind))
                questions += kind == "unexpected-question"
                time.sleep(2)
                continue
            if s != last:
                last, stable_since = s, time.time()
            elif worked and time.time() - stable_since >= idle_s and not self.busy(s) and re.search(self.ready, s) \
                    and self.turn_over(s, prompt):
                ok = self.evidence(task, cycle, t_prompt, calls_before, screen=s, prompt=prompt)
                self.end("done" if ok else "no-evidence", cycle, task, f"c{cycle}-{task['id']}", s, prompt,
                         seconds=round(time.time() - t0, 1))
                return ok
        self.end("timeout", cycle, task, f"c{cycle}-{task['id']}-timeout", s, prompt)
        return False

    def run(self, minutes, cfg):
        """Returns (cycles, tasks prompted, tasks done). A cycle whose client never gets ready counts all its tasks as failed,
        its known-server task included (D28 point 5). After a hard failure the cycle ends: its remaining tasks count as prompted
        and failed, so that nothing is typed into a client in an unknown state."""
        deadline = time.time() + minutes * 60
        cycle = prompted = done = 0
        while time.time() < deadline:
            cycle += 1
            q3.launch(self.spec["cmd"])
            self.last_prompt = None  # a fresh client: no earlier turn to wait for (D30)
            self.log(action="open", cycle=cycle)
            ready = q3.wait_ready({**self.spec, "ready": self.ready}, 90, [], f"c{cycle}", self.out / "screens", {"client": self.client, "signal": "baseline", "rep": cycle})
            self.quota_hit(q3.screen(), cycle, None)  # a quota message can sit next to the ready marker
            if not ready:
                self.log(action="not-ready", cycle=cycle)
                for task in self.tasks:
                    prompted += 1
                    self.server_prompts += task.get("evidence") == "server-call"
                    self.log(action="skipped-not-ready", cycle=cycle, task=task["id"])
            else:
                for i, task in enumerate(self.tasks):
                    if time.time() >= deadline or self.quota_message:
                        break
                    prompted += 1
                    self.server_prompts += task.get("evidence") == "server-call"
                    if self.do_task(cycle, task, cfg["task_timeout_s"], cfg["idle_s"]):
                        done += 1
                    elif self.fail:
                        for rest in self.tasks[i + 1:]:
                            prompted += 1
                            self.server_prompts += rest.get("evidence") == "server-call"
                            self.log(action="skipped-after-failure", cycle=cycle, task=rest["id"], after=self.fail)
                        break
            self.quota_hit(q3.screen(), cycle, None)  # last look before the screen is destroyed, deadline exits included
            q3.kill()
            self.log(action="close", cycle=cycle)
            if self.quota_message:
                break  # stop spending: the session is not valid anyway
            time.sleep(cfg["pause_between_cycles_s"])
        return cycle, prompted, done


def workload(cycles, prompted, done, calls, crit, server_cycles=None):
    """Whether the normal use happened: share of tasks done and known-server calls per cycle, against fixed thresholds.
    The calls are counted per cycle that reached the known-server task (server_cycles): a last cycle cut by the session's end
    before that task asked for no call (set-up of 22 Oct 2026, D28 proposal). Without server_cycles, every cycle counts."""
    share = done / prompted if prompted else 0.0
    denom = cycles if server_cycles is None else server_cycles
    per_cycle = calls / denom if denom else 0.0
    return {"cycles": cycles, "server_cycles": denom, "tasks_prompted": prompted, "tasks_done": done, "done_share": share,
            "known_server_calls": calls, "known_server_calls_per_cycle": per_cycle,
            "ok": share >= crit["min_done_share"] and per_cycle >= crit["min_known_server_calls_per_cycle"]}


def validity(events):
    """Definitions served by tools/list in the session, and whether the server logged any change (the server's log records
    every message out with the definition state at that moment)."""
    served = sorted({e.get("state") for e in events if e.get("event") == "out" and e.get("why") == "tools/list"})
    return served, any(e.get("event") == "definition-changed" for e in events)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True, choices=list(q3.CLIENTS))
    ap.add_argument("--part", required=True, choices=["setup", "dev", "holdout"])
    ap.add_argument("--minutes", type=float, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    q3.load_overrides(TEL.parent / "tool-change/scripts/q3-clients.json")
    cfg = json.loads((TEL / "baseline/tasks.json").read_text())
    perms = json.loads((TEL / "baseline/clients.json").read_text())
    outdir = Path(a.out).expanduser() / f"baseline__{a.client}__{a.part}"
    (outdir / "screens").mkdir(parents=True, exist_ok=True)
    for name, text in cfg["setup_files"].items():
        (q3.REPO / name).write_text(text)
    q3.flip_init("none", "description", f"baseline-{a.client}-{a.part}")  # definition A, no signal, for the whole session
    s = Session(a.client, a.part, outdir, cfg["tasks"], perms, cell=f"baseline-{a.client}-{a.part}")
    t_start = time.time()
    s.log(action="session-start", client=a.client, part=a.part, minutes=a.minutes,
          version=q3.sh(q3.CLIENTS[a.client]["version"] + " 2>&1 | head -1").stdout.strip())
    cycles, prompted, done = s.run(a.minutes, cfg)
    events = q3.events()
    served, changed = validity(events)
    calls = sum(1 for e in events if e.get("event") == "tools/call" and e.get("cell") == f"baseline-{a.client}-{a.part}")
    w = workload(cycles, prompted, done, calls, cfg["workload_success"], server_cycles=s.server_prompts)
    s.log(action="session-end", cycles=cycles, definitions_served=served, definition_changed=changed, workload=w)
    (outdir / "session.json").write_text(json.dumps({"client": a.client, "part": a.part, "minutes": a.minutes, "t_start": t_start,
                                                     "t_end": time.time(), "definitions_served": served,
                                                     "definition_changed": changed, "workload": w,
                                                     "quota_exhausted": bool(s.quota_message), "quota_message": s.quota_message,
                                                     "error_replies": s.error_replies,
                                                     "valid": served in ([], ["A"]) and not changed and w["ok"]
                                                     and not s.quota_message}, indent=1))


if __name__ == "__main__":
    main()
