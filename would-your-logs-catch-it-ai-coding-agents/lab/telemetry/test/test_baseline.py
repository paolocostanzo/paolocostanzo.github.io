"""baseline_session.py without a VM: question answering, task completion, session validity (D24 point 4).
Run: python3 -m unittest discover -s telemetry/test"""
import importlib.util, json, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("baseline_session", SCRIPTS / "baseline_session.py")
bs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bs)
PERMS = json.loads((SCRIPTS.parent / "baseline/clients.json").read_text())
CFG = json.loads((SCRIPTS.parent / "baseline/tasks.json").read_text())


class FakeTmux:
    """Replaces the tmux helpers of run_q3_cell with a scripted sequence of screens."""
    def __init__(self, screens):
        self.screens, self.sent = list(screens), []
        bs.q3.screen = lambda: self.screens[0] if len(self.screens) == 1 else self.screens.pop(0)
        bs.q3.keys = lambda *k: self.sent.append(k)
        bs.q3.type_text = lambda text: self.sent.append(("TYPE", text))
        bs.type_literal = lambda text: self.sent.append(("TYPE", text))
        bs.q3.answer_noise = lambda s: False
        bs.time.sleep = lambda s: None

    def switch(self, before, after):
        """`before` until the prompt's text is typed, then the screens in `after` (the last one stays)."""
        after = list(after)
        bs.q3.screen = lambda: before if not any(k[0] == "TYPE" for k in self.sent) else (after.pop(0) if len(after) > 1 else after[0])


class Baseline(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        (self.out / "screens").mkdir()
        # FakeTmux and the clock below patch shared modules: restore them after every test
        saved_q3 = {n: getattr(bs.q3, n) for n in ("screen", "keys", "type_text", "answer_noise", "launch", "kill", "wait_ready")}
        saved_type = bs.type_literal
        self.addCleanup(lambda: setattr(bs, "type_literal", saved_type))
        saved_time = (bs.time.time, bs.time.sleep)
        self.addCleanup(lambda: [setattr(bs.q3, n, f) for n, f in saved_q3.items()])
        self.addCleanup(lambda: (setattr(bs.time, "time", saved_time[0]), setattr(bs.time, "sleep", saved_time[1])))

    def session(self, client):
        bs.q3.load_overrides(SCRIPTS.parent.parent / "tool-change/scripts/q3-clients.json")
        return bs.Session(client, "dev", self.out, CFG["tasks"], PERMS)

    def test_allow_once_and_mcp_approval_use_the_fixed_keys(self):
        s = self.session("codex")
        fake = FakeTmux(["x"])
        self.assertEqual(s.answer_questions("Would you like to run the following command?\n 1. Yes", 1, CFG["tasks"][1]), "allow-once")
        self.assertEqual(fake.sent[-1], ("Enter",))
        codex_dialog = ('  Allow the q3-lab MCP server to run tool "q3_noop"?\n'
                        "  › 1. Allow                   Run the tool and continue\n"
                        "    2. Allow for this session  Run the tool and remember this choice for this session\n"
                        "    3. Always allow            Run the tool and remember this choice for future tool calls")
        self.assertEqual(s.answer_questions(codex_dialog, 1, CFG["tasks"][3]), "mcp-approval")
        self.assertEqual(fake.sent[-1], ("3", "Enter"))
        self.assertIsNone(s.answer_questions("greet.py returns a greeting.\n› Ask Codex to do anything", 1, CFG["tasks"][0]))

    def test_generic_question_only_at_the_bottom_of_the_screen(self):
        s = self.session("goose")
        FakeTmux(["x"])
        old = "Do you want to see more?\n" + "\n".join(f"line {i}" for i in range(30))
        self.assertIsNone(s.answer_questions(old, 1, CFG["tasks"][0]))
        self.assertEqual(s.answer_questions("output\nProceed? (y/n)", 1, CFG["tasks"][0]), "unexpected-question")

    def test_task_done_when_screen_is_stable_and_ready(self):
        s = self.session("codex")
        s.evidence = lambda task, cycle, t, c, **k: True
        ready = "greet.py defines greet().\n› Ask Codex to do anything"
        fake = FakeTmux(["x"])
        typed = "› " + CFG["tasks"][2]["prompt"].replace("{k}", "3")  # the composer before Enter (D34)
        fake.switch("› Ask Codex to do anything", [typed, "working...", "working...", ready])
        clock = iter(range(0, 1000, 5))
        bs.time.time = lambda: next(clock)
        self.assertTrue(s.do_task(3, CFG["tasks"][2], timeout=240, idle_s=8))
        self.assertIn("cycle 3", fake.sent[0][1], "{k} is replaced by the cycle number")
        lines = [json.loads(l) for l in (self.out / "timeline.jsonl").read_text().splitlines()]
        self.assertEqual([l["action"] for l in lines], ["prompt", "done"])

    def test_auto_mode_notice_is_not_a_question(self):
        """Set-up of 22 Oct 2026: "Allowed by auto mode classifier" matched the generic "Allow" and Enter was sent for 4 min."""
        s = self.session("claude-code")
        FakeTmux(["x"])
        screen = ("● Bash(python3 -m unittest -q)\n  ⎿  Ran 1 test\n  ⎿  Allowed by auto mode classifier\n"
                  "● Yes, the tests pass.\n❯ show me the test file\n  ⏵⏵ auto mode on (shift+tab to cycle)")
        self.assertIsNone(s.answer_questions(screen, 1, CFG["tasks"][1]))
        self.assertEqual(s.answer_questions("output\nAllow command?", 1, CFG["tasks"][1]), "unexpected-question")

    def test_an_error_reply_is_not_an_answer(self):
        """Set-up of 22 Oct 2026: error replies drawn at the end of the screen (Goose 503, Gemini CLI empty response)."""
        s = self.session("goose")
        task = CFG["tasks"][0]
        prompt = task["prompt"]
        goose = (f"> {prompt}\n  ▸ shell\n    command: cat greet.py\nRan into this error: Server error (503 Service Unavailable) "
                 "at https://x: This model is currently experiencing high demand.\n  ⏱ 33.37s\n> ")
        self.assertFalse(s.evidence(task, 1, 0, 0, screen=goose, prompt=prompt))
        gemini = f"> {prompt}\n✕ The model returned an empty response. This may be a transient API issue.\n\n  Type your message"
        self.assertFalse(s.evidence(task, 1, 0, 0, screen=gemini, prompt=prompt))
        retried = (f"> {prompt}\nℹ This request failed. Retrying...\n✦ greet.py defines one function that returns a greeting.\n"
                   "  Type your message\n ~/q3-repo   gemini-flash-lite-latest")
        self.assertTrue(s.evidence(task, 1, 0, 0, screen=retried, prompt=prompt), "a short answer after an error is an answer")
        footer = (f"> {prompt}\nRan into this error: Server error (503 Service Unavailable)\n  ⏱ 3.1s\n" + "  ╌╌╌╌╌╌ 1% 7k/1.0M\n" * 14
                  + "> ")
        self.assertFalse(s.evidence(task, 1, 0, 0, screen=footer, prompt=prompt), "screen furniture after an error is not an answer")
        self.assertEqual(bs.turn_outcome(f"> {prompt}\n> ", prompt), "empty")
        real = (f"> {prompt}\n  ✓ extensions ready\n  ▸ shell\n    command: cat greet.py\n    def greet(name):\n"
                "Ran into this error: Server error (503 Service Unavailable) at https://generativelanguage.googleapis.com/v1beta/"
                "models/gemini-flash-lite-latest:streamGenerateContent: This model is currently experiencing high demand. Spikes "
                "in demand are usually temporary. Please try again later..\nPlease retry if you think this is a transient or "
                "recoverable error.\n  ⏱ 33.37s\n  ╌╌╌╌╌╌╌╌ 1% 7k/1.0M\n> ")
        self.assertEqual(bs.turn_outcome(real, prompt), "error", "Goose's complete error block (setup-r1 screen 0002)")
        gem = (f"> {prompt}\n✕ The model returned an empty response with no text or thoughts. This may be a transient API issue; "
               "please try again.\nℹ This request failed. Press F12 for diagnostics.\n ⠇ This is taking a bit longer, we're still "
               "on it. (esc to cancel, 2m 47s)\n Shift+Tab to accept edits\n >   Type your message or @path/to/file\n"
               " workspace (/directory)        sandbox\n ~/q3-repo                     no sandbox")
        self.assertEqual(bs.turn_outcome(gem, prompt), "error", "Gemini CLI's footer is not an answer (setup-r3)")
        earlier = f"Ran into this error: Server error (503 Service Unavailable)\n> {prompt}\ngreet.py returns a greeting.\n> "
        self.assertTrue(s.evidence(task, 1, 0, 0, screen=earlier, prompt=prompt), "an error before this prompt does not count")

    def clocked(self):
        clock = iter(range(0, 100000, 2))
        bs.time.time = lambda: next(clock)

    def test_a_busy_client_is_not_done_even_when_its_ready_marker_is_drawn(self):
        """Set-up of 22 Oct 2026: Goose keeps "goose is ready" on screen while it works; "explain" was declared done mid-reply."""
        s = self.session("goose")
        s.evidence = lambda *a, **k: True
        banner = "   \\____)    20261022_1 · /home/lab/q3-repo\n     L L     goose is ready\n"
        idle = banner + "> "
        busy = banner + "> Explain in two sentences what greet.py does.\n◐  Hatching clever solutions...  (Ctrl+C to interrupt)"
        done = banner + "> Explain in two sentences what greet.py does.\ngreet.py returns a greeting.\n  ⏱ 12.0s\n> "
        fake = FakeTmux([idle])
        after = [idle + CFG["tasks"][0]["prompt"]] + [busy] * 20 + [done]
        bs.q3.screen = lambda: idle if not any(k[0] == "TYPE" for k in fake.sent) else (after.pop(0) if len(after) > 1 else after[0])
        self.clocked()
        self.assertTrue(s.do_task(1, CFG["tasks"][0], timeout=240, idle_s=8))
        lines = [json.loads(l) for l in (self.out / "timeline.jsonl").read_text().splitlines()]
        self.assertEqual(lines[-1]["action"], "done")
        self.assertIn("greet.py returns a greeting", (self.out / "screens" / lines[-1]["screen"]).read_text())

    def test_an_unsent_prompt_fails_fast_and_the_notice_is_answered_at_most_twice(self):
        """Set-up of 22 Oct 2026 (Codex CLI 0.162.1, screen 0014): the account card stayed drawn, the prompt sat unsent in the
        composer, and Escape was sent at every poll for four minutes."""
        s = self.session("codex")
        card = ("  To get started, describe a task or try one of these commands:\n  Set up security for Daybreak mode\n"
                "  Set up Advanced Account Security with a hardware security key.\n› 1. Set up security\n"
                "  Press a number to choose · esc to dismiss · type to continue\n")
        stuck = card + "› Explain in two sentences what greet.py does. Do not change any file.\n  GPT-6.1-Sol default · ~/q3-repo"
        fake = FakeTmux(["x"])
        fake.switch("  To get started, describe a task\n› Ask Codex to do anything\n  GPT-6.1-Sol default · ~/q3-repo", [stuck])
        self.clocked()
        self.assertFalse(s.do_task(4, CFG["tasks"][0], timeout=240, idle_s=8))
        actions = [json.loads(l)["action"] for l in (self.out / "timeline.jsonl").read_text().splitlines()]
        self.assertIn("prompt-not-submitted", actions)
        self.assertNotIn("timeout", actions, "it fails fast, not after the task's timeout")
        self.assertLessEqual(sum(1 for k in fake.sent if k == ("Escape",)), 2, "once before the prompt, once while the task runs")

    def test_no_notice_answer_while_the_client_works(self):
        s = self.session("codex")
        FakeTmux(["x"])
        working = "Set up security for Daybreak mode\n• Working (12s • esc to interrupt)"
        self.assertFalse(s.answer_notice(working, 1, "explain"))

    def test_unexpected_questions_are_capped(self):
        s = self.session("goose")
        s.evidence = lambda *a, **k: True
        fake = FakeTmux(["x"])
        fake.switch("     L L     goose is ready\n> ", ["> " + CFG["tasks"][0]["prompt"], "working", "output\nProceed? (y/n)"])
        self.clocked()
        self.assertFalse(s.do_task(1, CFG["tasks"][0], timeout=240, idle_s=8))
        self.assertEqual(sum(1 for k in fake.sent if k == ("Enter",)), bs.MAX_QUESTIONS + 1, "the prompt's Enter, then the answers")
        actions = [json.loads(l)["action"] for l in (self.out / "timeline.jsonl").read_text().splitlines()]
        self.assertEqual(actions[-1], "question-limit")
        self.assertEqual(sum(1 for k in fake.sent if k == ("Enter",)) - 1, 3, "three answered, the fourth not answered")

    def test_old_spinner_lines_above_an_ended_turn_are_not_busy(self):
        """Codex review of D28, R1 (Goose screen 0007): old working lines stay in the transcript after the turn ended."""
        s = self.session("goose")
        ended = ("◐  Hatching clever solutions...  (Ctrl+C to interrupt)\n" * 4
                 + "Ran into this error: Server error (503 Service Unavailable)\n  ⏱ 33.37s\n  ╌╌╌╌ 1% 7k/1.0M\n> ")
        self.assertFalse(s.busy(ended))
        self.assertTrue(s.busy(ended + "Run the tests\n◐  Debugging reality...  (Ctrl+C to interrupt)"))

    def test_nothing_is_typed_into_a_client_that_never_settles(self):
        """Codex review of D28, R2: after the settle timeout the driver types nothing and the cycle ends."""
        s = self.session("goose")
        fake = FakeTmux(["     L L     goose is ready\n◐  Debugging reality...  (Ctrl+C to interrupt)"])
        self.clocked()
        self.assertFalse(s.do_task(1, CFG["tasks"][0], timeout=240, idle_s=8))
        self.assertFalse(any(k[0] == "TYPE" for k in fake.sent))
        self.assertEqual(s.fail, "not-settled")

    def test_an_unsent_prompt_on_a_screen_with_a_permanent_ready_marker_is_not_done(self):
        """Codex review of D28, R3: Goose's banner is always "ready"; a prompt that never left the composer is not an answer."""
        s = self.session("goose")
        fake = FakeTmux(["x"])
        idle = "     L L     goose is ready\n> "
        fake.switch(idle, [idle + "Explain in two sentences what greet.py does. Do not change any file."])
        self.clocked()
        self.assertFalse(s.do_task(1, CFG["tasks"][0], timeout=240, idle_s=8))
        self.assertEqual(s.fail, "prompt-not-submitted")

    def test_a_hard_failure_ends_the_cycle_and_counts_the_rest(self):
        """Codex review of D28, R2 and R6: after a hard failure nothing more is typed in that cycle; the remaining tasks count as
        prompted and failed, the known-server task included; a cycle that never gets ready counts its server task too."""
        s = self.session("goose")
        calls = []
        def fake_task(cycle, task, timeout, idle_s):
            calls.append(task["id"])
            s.fail = "timeout"
            return False
        s.do_task = fake_task
        clock = iter(range(0, 100000, 30))
        bs.time.time = lambda: next(clock)
        FakeTmux(["x"])
        bs.q3.launch = lambda cmd: None
        bs.q3.kill = lambda: None
        bs.q3.wait_ready = lambda *a, **k: True
        cycles, prompted, done = s.run(10, CFG)
        self.assertGreater(cycles, 1)
        self.assertEqual(calls, ["explain"] * cycles, "only the first task is typed in each cycle")
        self.assertEqual((prompted, done, s.server_prompts), (4 * cycles, 0, cycles))
        s2 = self.session("goose")
        bs.q3.wait_ready = lambda *a, **k: False
        clock2 = iter(range(0, 100000, 100))
        bs.time.time = lambda: next(clock2)
        c2, p2, d2 = s2.run(10, CFG)
        self.assertEqual((p2, d2, s2.server_prompts), (4 * c2, 0, c2))

    def test_goose_is_ready_at_its_composer_after_the_banner_scrolled_away(self):
        """setup-r2 (22 Oct 2026), Goose screen 0003: the edit was made and the turn ended, but the Q3 ready pattern found neither
        the banner nor a bare ">" and the task timed out (D29: clients.json "ready" for the baseline)."""
        s = self.session("goose")
        ended = ("    Edited greet.py (2 lines -> 4 lines)\nThe tests pass successfully.\n  ⏱ 21.38s\n"
                 "  ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ 1% 7k/1.0M\n> Enter to send · Ctrl+J newline")
        self.assertIsNone(bs.re.search(bs.q3.CLIENTS["goose"]["ready"], ended), "the frozen Q3 pattern misses it")
        self.assertTrue(bs.re.search(s.ready, ended))
        self.assertFalse(s.busy(ended))
        self.assertEqual(self.session("codex").ready, bs.q3.CLIENTS["codex"]["ready"], "other clients keep the Q3 pattern")

    def test_goose_turn_is_over_only_after_its_elapsed_time_line(self):
        """setup-r3 (22 Oct 2026): Goose with a slow model stayed still mid-turn with no working line; "explain" was declared over
        at 47 s of a 73 s turn and the next prompt waited unsent in its input line (D30: clients.json "turn_end")."""
        s = self.session("goose")
        prompt = CFG["tasks"][0]["prompt"]
        mid = (f"> {prompt}\n  ✓ extensions ready\n  ▸ shell\n    command: cat greet.py\n    def greet(name):\n"
               "> Enter to send · Ctrl+J newline")
        self.assertFalse(s.turn_over(mid, prompt))
        over = mid.replace("> Enter to send", "greet.py returns a greeting.\n  ⏱ 1m 13s\n  ╌╌╌ 1% 7k/1.0M\n> Enter to send")
        self.assertTrue(s.turn_over(over, prompt))
        nxt = CFG["tasks"][1]["prompt"]
        queued = over.replace("> Enter to send · Ctrl+J newline", f"> {nxt}")
        self.assertFalse(s.turn_over(queued, nxt), "an echo on the last drawn line: its turn has just started")
        plain = mid.replace("> Enter to send · Ctrl+J newline", f"{nxt}\n  ⏱ 1m 13s\n> Enter to send · Ctrl+J newline")
        self.assertFalse(s.turn_over(plain, nxt), "a queued copy (no echo) under an earlier turn's elapsed time is not over")
        self.assertTrue(self.session("codex").turn_over(mid, prompt), "other clients have no turn-end line")
        # settle() waits for the previous turn's end before the next prompt is typed
        s.last_prompt = prompt
        FakeTmux([mid])
        self.clocked()
        self.assertFalse(s.settle(1, "test", idle_s=8))

    def test_a_relaunched_client_does_not_wait_for_the_previous_cycle(self):
        """Adversarial review of D30, F1: last_prompt is cleared when the client is launched again."""
        s = self.session("goose")
        seen = []
        def fake_task(cycle, task, timeout, idle_s):
            seen.append(s.last_prompt)
            s.last_prompt = task["prompt"]
            s.fail = None
            return True
        s.do_task = fake_task
        clock = iter(range(0, 100000, 20))
        bs.time.time = lambda: next(clock)
        FakeTmux(["x"])
        bs.q3.launch = lambda cmd: None
        bs.q3.kill = lambda: None
        bs.q3.wait_ready = lambda *a, **k: True
        cycles, _, _ = s.run(10, CFG)
        self.assertGreater(cycles, 1)
        firsts = [seen[i] for i in range(0, len(seen), 4)]
        self.assertTrue(all(p is None for p in firsts), "the first task of every cycle starts with no previous prompt")

    def test_qwen_is_ready_when_a_suggestion_replaces_its_placeholder(self):
        """baseline-r2 (23 Oct 2026, D33): Qwen Code showed a suggested prompt ("> run the tests") instead of "Type your message";
        the edit was done, and the task timed out 8 times out of 13. Its idle footer starts with "Auto mode"; while it works the
        footer starts with "Enter to steer"."""
        s = self.session("qwen")
        idle = ("  ◆︎ I have appended # reviewed in cycle 13 to greet.py.\n> run the tests\n"
                "  ➜ repo · model · 1.0m Context 1.7% used\n  Auto mode (shift + tab to cycle)")
        working = "  ➜ repo · model\n  Enter to steer · Ctrl+Q to queue · Auto mode (shift + tab to cycle)"
        self.assertTrue(bs.re.search(s.ready, idle))
        self.assertFalse(bs.re.search(s.ready, working))
        self.assertTrue(bs.re.search(s.ready, ">   Type your message or @path/to/file"))
        self.assertEqual(self.session("gemini").ready, bs.q3.CLIENTS["gemini"]["ready"])

    def test_qwen_feedback_survey_is_dismissed_before_typing(self):
        """setup-r5 (23 Oct 2026, D34): with Qwen Code's survey open, the "3" keys of "q3_noop" answered it and vanished from the
        prompt. Its "0: Dismiss" is pressed before the prompt is typed."""
        s = self.session("qwen")
        fake = FakeTmux(["x"])
        survey = ("  ◆︎ I have appended the comment.\n●︎ How is Qwen doing this session? (optional)\n"
                  "1: Good 2: Bad 3: Fine 0: Dismiss\n> run the tests\n  ➜ repo · model · Context 1.7% used\n"
                  "  Auto mode (shift + tab to cycle)")
        self.assertTrue(s.answer_notice(survey, 1, "known-server"))
        self.assertEqual(fake.sent[-1], ("0",))
        self.assertFalse(self.session("codex").answer_notice(survey, 1, "known-server"), "only Qwen Code's list has it")

    QWEN_IDLE = "  ◆︎ I have appended the comment.\n> Type your message or @path/to/file\n  Auto mode (shift + tab to cycle)"
    QWEN_SURVEY = ("  ◆︎ I have appended the comment.\n●︎ How is Qwen doing this session? (optional)\n"
                   "1: Good 2: Bad 3: Fine 0: Dismiss\n> Type your message or @path/to/file\n  Auto mode (shift + tab to cycle)")

    def test_a_survey_that_stays_drawn_means_nothing_is_typed(self):
        """Adversarial review of D34: the survey can be drawn after the idle check; it is looked for again right before typing,
        answered at most twice, and while it stays drawn nothing is typed."""
        s = self.session("qwen")
        fake = FakeTmux([self.QWEN_SURVEY])
        self.clocked()
        self.assertFalse(s.do_task(1, CFG["tasks"][3], timeout=240, idle_s=8))
        self.assertEqual(s.fail, "not-settled")
        self.assertFalse(any(k[0] == "TYPE" for k in fake.sent))
        self.assertEqual(sum(1 for k in fake.sent if k == ("0",)), 2)

    def test_a_dismissed_survey_lets_the_prompt_through(self):
        s = self.session("qwen")
        s.evidence = lambda *a, **k: True
        prompt = CFG["tasks"][3]["prompt"]
        typed = self.QWEN_IDLE.replace("Type your message or @path/to/file", prompt)
        done = self.QWEN_IDLE.replace("I have appended the comment.", "ok")
        fake = FakeTmux(["x"])
        after = [typed, typed, done]
        bs.q3.screen = lambda: (self.QWEN_SURVEY if ("0",) not in fake.sent else self.QWEN_IDLE) \
            if not any(k[0] == "TYPE" for k in fake.sent) else (after.pop(0) if len(after) > 1 else after[0])
        self.clocked()
        self.assertTrue(s.do_task(1, CFG["tasks"][3], timeout=240, idle_s=8))
        self.assertEqual(fake.sent.index(("0",)) < [k[0] for k in fake.sent].index("TYPE"), True)

    def test_a_client_that_turns_busy_after_a_dismissed_survey_gets_no_prompt(self):
        """Codex review of D34, D34-02: after a notice is answered the client must settle again; a busy screen is not ready."""
        s = self.session("qwen")
        busy = self.QWEN_IDLE + "\n⠋ Thinking… (esc to cancel, 2s)"
        fake = FakeTmux(["x"])
        bs.q3.screen = lambda: self.QWEN_SURVEY if ("0",) not in fake.sent else busy
        self.clocked()
        self.assertFalse(s.do_task(1, CFG["tasks"][3], timeout=240, idle_s=8))
        self.assertEqual(s.fail, "not-settled")
        self.assertFalse(any(k[0] == "TYPE" for k in fake.sent))

    def test_an_input_line_drawn_in_steps_is_read_again(self):
        """Adversarial review of D35: a slow client may draw the prompt in steps; the check reads it again while it changes."""
        s = self.session("qwen")
        s.evidence = lambda *a, **k: True
        prompt = CFG["tasks"][0]["prompt"]
        half = self.QWEN_IDLE.replace("Type your message or @path/to/file", prompt[:30])
        whole = self.QWEN_IDLE.replace("Type your message or @path/to/file", prompt)
        done = self.QWEN_IDLE.replace("I have appended the comment.", "greet.py returns a greeting.")
        fake = FakeTmux(["x"])
        fake.switch(self.QWEN_IDLE, [half, whole, whole, done])
        self.clocked()
        self.assertTrue(s.do_task(1, CFG["tasks"][0], timeout=240, idle_s=8))
        self.assertIn(("Enter",), fake.sent)

    def test_a_mangled_prompt_is_not_sent(self):
        """setup-r5 (D34): "Call the q3_noop tool of the q3-lab MCP server" reached Qwen Code as "Call the q_noop tool of the
        q-lab MCP server". An altered prompt is never sent: no Enter, and the cycle ends."""
        s = self.session("qwen")
        prompt = CFG["tasks"][3]["prompt"]
        fake = FakeTmux(["x"])
        fake.switch(self.QWEN_IDLE, [self.QWEN_IDLE.replace("Type your message or @path/to/file", prompt.replace("3", ""))])
        self.clocked()
        self.assertFalse(s.do_task(1, CFG["tasks"][3], timeout=240, idle_s=8))
        self.assertEqual(s.fail, "prompt-mangled")
        self.assertIn("prompt-mangled", bs.HARD_FAILURES)
        self.assertNotIn(("Enter",), fake.sent)

    def test_composer_check_ignores_old_echoes_and_reads_wrapped_boxes(self):
        p = CFG["tasks"][0]["prompt"]
        before = f"> {p}\ngreet.py returns a greeting.\n  ⏱ 3.1s\n> "
        self.assertTrue(bs.composer_holds(before, f"> {p}\ngreet.py returns a greeting.\n  ⏱ 3.1s\n> {p}", p))
        self.assertFalse(bs.composer_holds(before, f"> {p}\ngreet.py returns a greeting.\n  ⏱ 3.1s\n> Explain in two", p),
                         "the echo of an earlier cycle does not stand for the composer")
        boxed_before = "╭────────────────────────────────╮\n│ > Type your message            │\n╰────────────────────────────────╯"
        boxed = ("╭────────────────────────────────╮\n│ > Explain in two sentences what │\n│ greet.py does. Do not change    │\n"
                 "│ any file.                       │\n╰────────────────────────────────╯")
        self.assertTrue(bs.composer_holds(boxed_before, boxed, p))
        self.assertFalse(bs.composer_holds("> ", "> 0" + p, p), "a survey's 0 left before the prompt")
        self.assertTrue(bs.composer_holds("status: idle\n", "status: typing\n" + p, p), "a changed line above does not count")
        self.assertFalse(bs.composer_holds("> ", "> " + p + "0", p), "nor after it")
        self.assertFalse(bs.composer_holds("> ", "> /" + p, p), "only an input marker may stand before it")
        self.assertTrue(bs.composer_holds("> █", "> " + p + " █", p), "Qwen Code's scroll bar")
        self.assertTrue(bs.composer_holds("> ", "> " + p + " \u200b", p), "Qwen Code's cursor (setup-r6)")
        k = CFG["tasks"][3]["prompt"]
        self.assertFalse(bs.composer_holds("> ", "> " + k.replace("q3", "q\u200b3", 1), k), "a zero-width character inside fails")
        self.assertFalse(bs.composer_holds("> ", "> " + k.replace("q3", "q\u200b\n3", 1), k), "also at a line end inside the input")
        self.assertFalse(bs.composer_holds("│ >    │", "│ > " + k.replace("q3", "q\u200b   │\n│ 3", 1) + " │", k), "boxed")
        self.assertTrue(bs.composer_holds("│ >    │", "│ > " + k.replace("q3", "q   │\n│ 3", 1) + " \u200b │", k), "cursor in a box")

    def test_validity(self):
        ok = [{"event": "out", "why": "tools/list", "state": "A"}, {"event": "tools/call", "state": "A"}]
        self.assertEqual(bs.validity(ok), (["A"], False))
        bad = ok + [{"event": "definition-changed", "state": "B"}, {"event": "out", "why": "tools/list", "state": "B"}]
        self.assertEqual(bs.validity(bad), (["A", "B"], True))


if __name__ == "__main__":
    unittest.main()


class Workload(unittest.TestCase):
    CRIT = {"min_done_share": 0.8, "min_known_server_calls_per_cycle": 0.8}

    def test_thresholds(self):
        self.assertTrue(bs.workload(10, 40, 32, 8, self.CRIT)["ok"])
        self.assertFalse(bs.workload(10, 40, 31, 8, self.CRIT)["ok"], "too few tasks completed")
        self.assertFalse(bs.workload(10, 40, 40, 7, self.CRIT)["ok"], "too few calls to the known server")
        self.assertFalse(bs.workload(0, 0, 0, 0, self.CRIT)["ok"], "a session with no use is not valid")

    def test_a_cycle_cut_before_the_known_server_task_asks_for_no_call(self):
        """Set-up of 22 Oct 2026: Codex, Gemini CLI and Cursor CLI did every task they were given, but the last cycle ended before
        the known-server task, and calls per cycle fell to 0.75 or 0.5 (D28 proposal)."""
        self.assertFalse(bs.workload(4, 15, 15, 3, self.CRIT)["ok"], "old denominator: every cycle")
        w = bs.workload(4, 15, 15, 3, self.CRIT, server_cycles=3)
        self.assertTrue(w["ok"])
        self.assertEqual((w["cycles"], w["server_cycles"], w["known_server_calls_per_cycle"]), (4, 3, 1.0))
        self.assertFalse(bs.workload(4, 16, 16, 2, self.CRIT, server_cycles=4)["ok"], "a reached task without its call still counts")
        self.assertFalse(bs.workload(1, 2, 2, 0, self.CRIT, server_cycles=0)["ok"], "no cycle reached the server task: no use")


class Evidence(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.saved = (bs.q3.REPO, bs.q3.events)
        bs.q3.REPO = self.repo
        self.addCleanup(lambda: (setattr(bs.q3, "REPO", self.saved[0]), setattr(bs.q3, "events", self.saved[1])))
        out = Path(tempfile.mkdtemp()); (out / "screens").mkdir()
        self.s = bs.Session("codex", "dev", out, CFG["tasks"], PERMS)
        self.task = {t["id"]: t for t in CFG["tasks"]}

    def test_edit_needs_the_line_in_the_file(self):
        (self.repo / "greet.py").write_text("def greet(n):\n    return n\n")
        self.assertFalse(self.s.evidence(self.task["edit"], 3, 0, 0), "a refused edit leaves the file as it was")
        (self.repo / "greet.py").write_text("def greet(n):\n    return n\n# reviewed in cycle 3\n")
        self.assertTrue(self.s.evidence(self.task["edit"], 3, 0, 0))
        self.assertFalse(self.s.evidence(self.task["edit"], 4, 0, 0))

    def test_tests_need_a_run_after_the_prompt(self):
        self.assertFalse(self.s.evidence(self.task["test"], 1, 100.0, 0))
        (self.repo / ".q4-test-runs").write_text("50.0\n")
        self.assertFalse(self.s.evidence(self.task["test"], 1, 100.0, 0), "an older run does not count")
        (self.repo / ".q4-test-runs").write_text("50.0\n100.5\n")
        self.assertTrue(self.s.evidence(self.task["test"], 1, 100.0, 0))

    def test_known_server_needs_a_new_call_of_this_session(self):
        bs.q3.events = lambda: [{"event": "tools/call", "cell": "baseline-codex-dev"}, {"event": "tools/call", "cell": "other"}]
        self.assertEqual(self.s.server_calls(), 1)
        self.assertFalse(self.s.evidence(self.task["known-server"], 1, 0, 1))
        self.assertTrue(self.s.evidence(self.task["known-server"], 1, 0, 0))

    def test_instrumented_test_file_writes_its_marker(self):
        files = CFG["setup_files"]
        (self.repo / "greet.py").write_text('def greet(name):\n    return f"hello, {name}"\n')
        (self.repo / "test_greet.py").write_text(files["test_greet.py"])
        r = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.repo / ".q4-test-runs").read_text().strip())


class OverlappingQuestions(unittest.TestCase):
    """Claude Code asks "Do you want to proceed?" both for a command and for an MCP tool; only the tool question may get the
    frozen Q3 approval (option 2, "don't ask again"), every command gets "allow once"."""
    def setUp(self):
        saved = {n: getattr(bs.q3, n) for n in ("keys",)}
        self.addCleanup(lambda: [setattr(bs.q3, n, f) for n, f in saved.items()])
        self.sent = []
        bs.q3.keys = lambda *k: self.sent.append(k)
        bs.q3.load_overrides(SCRIPTS.parent.parent / "tool-change/scripts/q3-clients.json")
        out = Path(tempfile.mkdtemp()); (out / "screens").mkdir()
        self.s = bs.Session("claude-code", "dev", out, CFG["tasks"], PERMS)

    def test_command_question_is_answered_once(self):
        screen = ("Bash command\n\n  python3 -m unittest -q\n  Run the tests\n\nDo you want to proceed?\n"
                  " ❯ 1. Yes\n   2. Yes, and don't ask again for python3 commands in /home/lab/q3-repo\n   3. No")
        self.assertEqual(self.s.answer_questions(screen, 1, CFG["tasks"][1]), "allow-once")
        self.assertEqual(self.sent[-1], ("Enter",), "option 1, never option 2")

    def test_lab_tool_question_gets_the_frozen_approval(self):
        screen = ("Tool use\n\n  q3-lab - q3_noop() (MCP)\n\nDo you want to proceed?\n ❯ 1. Yes\n   2. Yes, and don't ask again for q3-lab - q3_noop\n   3. No")
        self.assertEqual(self.s.answer_questions(screen, 1, CFG["tasks"][3]), "mcp-approval")
        self.assertEqual(self.sent[-1], ("2",))

    def test_old_lab_tool_text_above_does_not_count(self):
        screen = "earlier: q3_noop returned ok\n" + "\n".join(f"line {i}" for i in range(40)) + \
                 "\nBash command\n  python3 -m unittest -q\nDo you want to proceed?\n ❯ 1. Yes\n   2. Yes, and don't ask again"
        self.assertEqual(self.s.answer_questions(screen, 2, CFG["tasks"][1]), "allow-once")


class Quota(unittest.TestCase):
    def setUp(self):
        out = Path(tempfile.mkdtemp()); (out / "screens").mkdir()
        self.s = bs.Session("codex", "dev", out, CFG["tasks"], PERMS)
        saved = bs.q3.screen
        self.addCleanup(lambda: setattr(bs.q3, "screen", saved))
        bs.q3.screen = lambda: "x"

    def test_messages_are_recognised(self):
        for client, text in [("codex", "■ You've hit your usage limit. Upgrade or try again at 9:41 PM."),
                             ("claude-code", "Claude usage limit reached. Your limit will reset at 7pm"),
                             ("gemini", "API Error: [429] Quota exceeded for metric generate_content_free_tier_requests"),
                             ("qwen", "RESOURCE_EXHAUSTED"), ("goose", "Request failed: HTTP 429 Too Many Requests")]:
            self.assertIsNotNone(bs.quota.find(client, text), text)

    def test_codex_reset_notice_is_not_a_quota_message(self):
        """Baseline of 23 Oct 2026 (D32): Codex CLI 0.162.1 showed this notice at the start of a cycle, 40 minutes into a session,
        with no limit message on any screen."""
        self.assertIsNone(bs.quota.find("codex", "• You have 2 usage limit resets available. Run /usage to use one."))
        # D37: Codex CLI's tip is not a stop; a limit that says when it resets still is
        self.assertIsNone(bs.quota.find("codex", "└ Tip: When signed in with ChatGPT, use /usage to check your account usage and "
                                                 "access available usage limit resets."))
        self.assertTrue(bs.quota.find("codex", "Your usage limit resets in 3h"))
        self.assertTrue(bs.quota.find("codex", "■ You've hit your usage limit. Tip: upgrade to Pro"), "a stop before a tip still counts")
        self.assertTrue(bs.quota.find("goose", "error: rate limit exceeded; tip: retry later"), "a lower-case tip mid-line is kept")
        self.assertTrue(bs.quota.find("codex", "Tip: retry later. Request failed: HTTP 429 Too Many Requests"), "a failure after a tip")
        self.assertTrue(bs.quota.find("codex", "└ Tip: access available usage limit resets. Error: 429 Too Many Requests"),
                        "only the recorded phrase is removed")
        self.assertIsNone(bs.quota.find("codex", "  └ Tip: When signed in with ChatGPT, use /usage to check your account usage and access available\n"
                                                 "    usage limit resets."), "the tip wrapped")
        self.assertTrue(bs.quota.find("codex", "Usage limit reached for GPT-6.1-Sol"))
        # D35: Claude Code's footer notice is not a stop; its limit messages still are
        self.assertIsNone(bs.quota.find("claude-code", "esc to interrupt   You've used 76% of your weekly limit · resets Oct 24, 10pm"))
        self.assertIsNone(bs.quota.find("claude-code", "You've used 90% of your 5-hour limit · resets 11pm"))
        self.assertIsNone(bs.quota.find("claude-code", "You've used 76% of your\nweekly limit · resets Oct 24, 10pm"), "wrapped")
        self.assertIsNone(bs.quota.find("claude-code", "You've  used 80%  of your Opus weekly limit · resets"), "spacing, model")
        self.assertTrue(bs.quota.find("claude-code", "You've hit your limit · resets 10pm (Europe/Rome)"))
        self.assertTrue(bs.quota.find("claude-code", "Claude usage limit reached. Your limit will reset at 10pm"))
        self.assertTrue(bs.quota.find("goose", "Your prepayment credits are depleted. Please go to AI Studio"))
        for real in ("Your usage limit resets in 3h", "Usage limit resets at 17:00. Upgrade or wait.", "usage limit\nresets in 2h"):
            self.assertIsNotNone(bs.quota.find("goose", real), real)
        self.assertIsNotNone(bs.quota.find("gemini", "Usage limit reached for gemini-flash-lite-latest."))
        self.assertIsNotNone(bs.quota.find("cursor-agent", "Error: You've hit your usage limit"))

    def test_ordinary_text_is_not_a_quota_message(self):
        for text in ["greet.py returns a greeting for the given name.", "Ran 1 test in 0.001s\n\nOK", "line 429 of the log",
                     "the function has a limit on the name length", "# reviewed in cycle 4"]:
            self.assertIsNone(bs.quota.find("codex", text), text)

    def test_session_stops_and_records_the_message(self):
        self.assertTrue(self.s.quota_hit("You've hit your usage limit", 3, "edit"))
        self.assertEqual(self.s.quota_message, "You've hit your usage limit")
        lines = [json.loads(l) for l in (self.s.out / "timeline.jsonl").read_text().splitlines()]
        self.assertEqual(lines[-1]["action"], "quota")


class DialogDetection(OverlappingQuestions):
    def test_recent_tool_output_above_a_command_dialog(self):
        screen = ("⏺ q3-lab - q3_noop (MCP)\n  ⎿  ok\n\n⏺ ok\n\n> Run the tests\n\nBash command\n\n  python3 -m unittest -q\n"
                  "  Run the tests\n\nDo you want to proceed?\n ❯ 1. Yes\n   2. Yes, and don't ask again for python3 commands\n   3. No")
        self.assertEqual(self.s.answer_questions(screen, 2, CFG["tasks"][1]), "allow-once")
        self.assertEqual(self.sent[-1], ("Enter",))

    def test_lab_tool_name_inside_a_command(self):
        screen = "Bash command\n\n  grep -n q3_noop greet.py\n\nDo you want to proceed?\n ❯ 1. Yes\n   2. Yes, and don't ask again"
        self.assertEqual(self.s.answer_questions(screen, 2, CFG["tasks"][1]), "allow-once")


class QuotaAtCycleEdges(unittest.TestCase):
    def test_quota_next_to_the_ready_marker_invalidates(self):
        out = Path(tempfile.mkdtemp()); (out / "screens").mkdir()
        bs.q3.load_overrides(SCRIPTS.parent.parent / "tool-change/scripts/q3-clients.json")
        s = bs.Session("codex", "dev", out, CFG["tasks"], PERMS)
        saved = {n: getattr(bs.q3, n) for n in ("launch", "wait_ready", "screen", "kill")}
        saved_sleep = bs.time.sleep
        self.addCleanup(lambda: ([setattr(bs.q3, n, f) for n, f in saved.items()], setattr(bs.time, "sleep", saved_sleep)))
        bs.q3.launch = lambda cmd: None
        bs.q3.wait_ready = lambda *a, **k: True
        bs.q3.screen = lambda: "■ You've hit your usage limit.\n› Ask Codex to do anything"
        bs.q3.kill = lambda: None
        bs.time.sleep = lambda x: None
        cycles, prompted, done = s.run(0.05, CFG)
        self.assertEqual(cycles, 1, "the session ends at the first quota message")
        self.assertIsNotNone(s.quota_message)


class PositiveMcpIdentification(OverlappingQuestions):
    def test_long_command_pushes_its_heading_out_of_the_dialog_window(self):
        body = "\n".join(f"  echo step {i}" for i in range(14))
        screen = f"Bash command\n\n{body}\n  grep q3_noop greet.py\n\nDo you want to proceed?\n ❯ 1. Yes\n   2. Yes, and don't ask again\n   3. No"
        self.assertEqual(self.s.answer_questions(screen, 3, CFG["tasks"][1]), "allow-once")
        self.assertEqual(self.sent[-1], ("Enter",))

    def test_lab_tool_without_mcp_marker_is_not_trusted(self):
        screen = "Something about q3_noop\nDo you want to proceed?\n ❯ 1. Yes\n   2. Yes, and don't ask again"
        self.assertEqual(self.s.answer_questions(screen, 3, CFG["tasks"][3]), "allow-once")


class FullPaneCaptures(unittest.TestCase):
    """tmux captures the whole 50-line pane: the question can sit at line 20-24 with blank lines below (setup captures of
    15 Oct 2026, Gemini CLI and Cursor CLI). Synthetic replicas of those layouts."""
    def setUp(self):
        saved = bs.q3.keys
        self.addCleanup(lambda: setattr(bs.q3, "keys", saved))
        self.sent = []
        bs.q3.keys = lambda *k: self.sent.append(k)
        bs.q3.load_overrides(SCRIPTS.parent.parent / "tool-change/scripts/q3-clients.json")

    def session(self, client):
        out = Path(tempfile.mkdtemp()); (out / "screens").mkdir()
        return bs.Session(client, "dev", out, CFG["tasks"], PERMS)

    def test_gemini_question_above_blank_lines(self):
        head = ["", "", "", "", "", "", "", " > Call the q3_noop tool of the q3-lab MCP server once.", "", "",
                "  Calling q3_noop.", "", "", "", "", "", "│ ?  q3_noop (q3-lab MCP Server)", "│", "│ MCP Server: q3-lab",
                "│ Tool: q3_noop", "│", "│ MCP Tool Details:", "│ (press Ctrl+O to expand MCP tool details)",
                '│ Allow execution of MCP tool "q3_noop" from server "q3-lab"?', "│", "│ ● 1. Allow once", "│   2. Allow tool for this session"]
        screen = "\n".join(head + [""] * (50 - len(head)))
        self.assertEqual(self.session("gemini").answer_questions(screen, 1, CFG["tasks"][3]), "mcp-approval")
        self.assertEqual(self.sent[-1], ("2", "Enter"))

    def test_cursor_question_above_blank_lines(self):
        head = [""] * 7 + ["  Call the q3_noop tool of the q3-lab MCP server once.", "", "", "  I'll call q3_noop.", "",
                "    Explored available MCP tools q3-lab · q3_noop", "", "    q3-lab q3_noop", "", "", " q3-lab: q3_noop", "",
                " Run this MCP tool?", "", "    Allowlist MCP Tool (tab)", "    Run (y)", "    Skip (n)"]
        screen = "\n".join(head + [""] * (50 - len(head)))
        self.assertEqual(self.session("cursor-agent").answer_questions(screen, 1, CFG["tasks"][3]), "mcp-approval")
        self.assertEqual(self.sent[-1], ("Tab",))


class ExactMcpHeaders(OverlappingQuestions):
    def test_very_long_command_with_mcp_text_in_its_body(self):
        body = "\n".join(f"  echo step {i}" for i in range(30))
        screen = f"Bash command\n\n{body}\n  echo 'q3_noop (MCP)'\n\nDo you want to proceed?\n ❯ 1. Yes\n   2. Yes, and don't ask again\n   3. No"
        self.assertEqual(self.s.answer_questions(screen, 3, CFG["tasks"][1]), "allow-once", "heading cropped, no exact MCP header")

    def test_every_client_with_an_approval_has_an_exact_header(self):
        for client, spec in PERMS.items():
            if client.startswith("_") or client == "goose":
                continue
            self.assertTrue(spec.get("mcp_header"), client)


class OptionScope(OverlappingQuestions):
    def test_exact_header_inside_a_long_command(self):
        body = "\n".join(f"  echo step {i}" for i in range(30))
        screen = (f"Bash command\n\n{body}\n  q3-lab - q3_noop() (MCP)\n\nDo you want to proceed?\n ❯ 1. Yes\n"
                  "   2. Yes, and don't ask again for echo commands in /home/lab/q3-repo\n   3. No")
        self.assertEqual(self.s.answer_questions(screen, 3, CFG["tasks"][1]), "allow-once")
        self.assertEqual(self.sent[-1], ("Enter",))

    def test_mcp_dialog_whose_persistent_option_is_not_the_lab_tool(self):
        screen = ("Tool use\n\n  q3-lab - q3_noop() (MCP)\n\nDo you want to proceed?\n ❯ 1. Yes\n"
                  "   2. Yes, and don't ask again for all tools\n   3. No")
        self.assertEqual(self.s.answer_questions(screen, 3, CFG["tasks"][3]), "allow-once", "the option scope must be the lab tool")
