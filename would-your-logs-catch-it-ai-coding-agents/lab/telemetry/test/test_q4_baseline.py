"""q4_baseline.py: plan, commands and the holdout gap (D13 point 2, D24 point 4). No VM. Run: python3 -m unittest discover -s telemetry/test"""
import importlib.util, json, tempfile, time, unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("q4_baseline", SCRIPTS / "q4_baseline.py")
qb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qb)
qb.ev.STATE = Path(tempfile.mkdtemp())  # never the real ~/.agentlab-q4 of the host


class BaselinePlan(unittest.TestCase):
    def test_dev_block_then_holdout_block_in_the_same_order(self):
        p = qb.plan(11)
        self.assertEqual(len(p), 12)
        dev = [s["client"] for s in p if s["part"] == "dev"]
        hold = [s["client"] for s in p if s["part"] == "holdout"]
        self.assertEqual(dev, hold)
        self.assertEqual(sorted(dev), sorted(qb.ev.CLIENTS))
        self.assertEqual([s["part"] for s in p], ["dev"] * 6 + ["holdout"] * 6)
        self.assertEqual(qb.plan(11), p)

    def test_command(self):
        c = qb.session_command({"client": "gemini", "part": "holdout"}, 60)
        self.assertEqual(c, qb.ev.watched("python3 ~/lab/telemetry/scripts/baseline_session.py --client gemini --part holdout "
                                         "--minutes 60 --out ~/q4-out", "gemini", "q3run"))
        self.assertIn("quota_watch.py --client gemini --session q3run", c)

    def attempt(self, d, n, at, error_replies, ok=False, status="session-not-counted"):
        a = d / "attempts" / str(n); a.mkdir(parents=True)
        (a / "attempt.json").write_text(json.dumps({"status": status, "at": at,
                                                    "session": {"error_replies": error_replies, "workload": {"ok": ok}}}))

    def test_provider_unavailable_attempts_wait_one_hour_then_stop_the_client(self):
        """D28 point 6 (Codex review of D28, R7): enforced by the runner, not by hand."""
        out = Path(tempfile.mkdtemp())
        s = {"client": "gemini", "part": "dev", "name": "baseline__gemini__dev"}
        d = out / s["name"]
        notes, slept = [], []
        t = time.time()
        iso = lambda x: qb.dt.datetime.fromtimestamp(x, qb.dt.timezone.utc).isoformat(timespec="seconds")
        self.attempt(d, 1, iso(t - 600), 2)
        self.attempt(d, 2, iso(t - 7200), 0)                       # not a provider failure: no error reply
        self.attempt(d, 3, iso(t - 7300), 3, status="secret-found")  # another kind of attempt
        self.assertEqual(len(qb.provider_attempts(d)), 1)
        self.assertFalse(qb.provider_gate(out, s, lambda **k: notes.append(k), sleep=slept.append, now=lambda: t))
        self.assertAlmostEqual(slept[0], 3000, delta=2)
        self.assertEqual(notes[-1]["event"], "provider-wait")
        self.attempt(d, 4, iso(t - 5000), 1)
        self.attempt(d, 5, iso(t - 9000), 4)
        self.assertTrue(qb.provider_gate(out, s, lambda **k: notes.append(k), sleep=slept.append, now=lambda: t))
        self.assertIn("gemini", qb.incomplete_clients(out))
        self.assertEqual(notes[-1]["event"], "client-incomplete")
        self.assertFalse(qb.provider_gate(out, {"client": "gemini", "part": "setup", "name": "baseline__gemini__setup"},
                                          lambda **k: None), "set-up sessions are not limited")

    def test_quota_stops_and_recovered_sessions_in_the_provider_rule(self):
        """Codex review of D28, round 2: G2-02 (a quota stop is never a provider failure) and G2-03 (a recovered attempt counts)."""
        import io, tarfile
        d = Path(tempfile.mkdtemp()) / "baseline__qwen__dev"
        iso = qb.dt.datetime.now(qb.dt.timezone.utc).isoformat(timespec="seconds")
        a = d / "attempts" / "1"; a.mkdir(parents=True)
        (a / "attempt.json").write_text(json.dumps({"status": "session-not-counted", "at": iso,
                                                    "problems": ["quota or rate limit reached (observer): 'API Error: [429]' (stop)"],
                                                    "session": {"error_replies": 2, "workload": {"ok": False}}}))
        b = d / "attempts" / "2"; b.mkdir(parents=True)
        (b / "attempt.json").write_text(json.dumps({"status": "session-not-counted", "at": iso,
                                                    "session": {"error_replies": 1, "quota_exhausted": True, "workload": {"ok": False}}}))
        self.assertEqual(qb.provider_attempts(d), [], "quota stops keep D27 point 8")
        c = d / "attempts" / "3"; c.mkdir(parents=True)
        (c / "attempt.json").write_text(json.dumps({"status": "interrupted-recovered", "at": iso}))
        raw = json.dumps({"error_replies": 3, "workload": {"ok": False}}).encode()
        with tarfile.open(c / "vm-out.tgz", "w:gz") as tar:
            info = tarfile.TarInfo("q4-out/baseline__qwen__dev/session.json"); info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
        self.assertEqual(len(qb.provider_attempts(d)), 1, "a recovered provider failure counts")
        e = d / "attempts" / "4"; e.mkdir(parents=True)
        (e / "attempt.json").write_text(json.dumps({"status": "interrupted-recovered", "at": iso}))
        orig = qb.ev.observer_problems
        qb.ev.observer_problems = lambda text, bounds: ["quota or rate limit reached (observer): 'Quota exceeded' (stop)"] \
            if "Quota exceeded" in text else []
        self.addCleanup(lambda: setattr(qb.ev, "observer_problems", orig))
        with tarfile.open(e / "vm-out.tgz", "w:gz") as tar:
            for name, data in (("q4-out/baseline__qwen__dev/session.json", raw),
                               ("q4-out/quota-watch.jsonl", b'{"event": "quota", "message": "Quota exceeded"}\n')):
                info = tarfile.TarInfo(name); info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        self.assertEqual(len(qb.provider_attempts(d)), 1, "a recovered attempt with an observer quota event is a quota stop")

    def test_holdout_waits_for_the_gap(self):
        out = Path(tempfile.mkdtemp())
        (out / "baseline__codex__dev").mkdir()
        (out / "baseline__codex__dev" / "run.json").write_text(json.dumps({"t1": time.time() - 3600}))  # complete record
        slept, notes = [], []
        w = qb.wait_for_gap(out, {"client": "codex", "part": "holdout", "name": "x"}, 6, lambda **k: notes.append(k), slept.append)
        self.assertAlmostEqual(w, 5 * 3600, delta=5)
        self.assertAlmostEqual(slept[0], 5 * 3600, delta=5)
        self.assertEqual(qb.wait_for_gap(out, {"client": "codex", "part": "dev", "name": "y"}, 6, print, slept.append), 0)
        (out / "baseline__codex__dev" / "run.json").write_text(json.dumps({"t1": time.time() - 7 * 3600}))
        self.assertEqual(qb.wait_for_gap(out, {"client": "codex", "part": "holdout", "name": "x"}, 6, print, slept.append), 0)
        self.assertEqual(len(slept), 1)


if __name__ == "__main__":
    unittest.main()


import io as _io, tarfile as _tarfile


def _tgz(files):
    buf = _io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = _tarfile.TarInfo(name); info.size = len(data)
            tar.addfile(info, _io.BytesIO(data))
    return buf.getvalue()


class BaselineOutputs(unittest.TestCase):
    S = {"client": "codex", "part": "dev", "name": "baseline__codex__dev"}

    def files(self, session):
        b = "q4-out/baseline__codex__dev/"
        return {b + "session.json": json.dumps(session).encode(), b + "timeline.jsonl": b"", "q3-server.log": b"",
                "q4-out/quota-watch.jsonl": b'{"ts": 1, "event": "started"}\n{"ts": 5, "event": "heartbeat"}\n{"ts": 9, "event": "stopped"}\n',
                "q4-out/command-bounds.json": b'{"start": 2, "end": 8, "rc": 0, "observer_rc": 0}'}

    def test_valid_session_passes_and_invalid_is_not_counted(self):
        good = {"valid": True, "workload": {"ok": True}}
        sess, problems = qb.check_outputs(self.S, _tgz(self.files(good)))
        self.assertEqual(problems, [])
        _, problems = qb.check_outputs(self.S, _tgz(self.files({"valid": False, "workload": {"ok": False}})))
        self.assertTrue(problems)
        f = self.files(good); f.pop("q3-server.log")
        self.assertTrue(qb.check_outputs(self.S, _tgz(f))[1])

    def test_local_parts_do_not_exist(self):
        with self.assertRaises(SystemExit):
            qb.plan(1, parts=("local-dev", "local-holdout"))

    def test_holdout_without_a_valid_dev_session_stops(self):
        out = Path(tempfile.mkdtemp())
        with self.assertRaises(SystemExit):
            qb.wait_for_gap(out, {"client": "qwen", "part": "holdout", "name": "x"}, 6, print, lambda s: None)


class QuotaStops(BaselineOutputs):
    def test_quota_session_is_not_counted(self):
        _, problems = qb.check_outputs(self.S, _tgz(self.files({"valid": False, "quota_exhausted": True,
                                                                 "quota_message": "usage limit", "workload": {"ok": True}})))
        self.assertTrue(any("quota" in p for p in problems))


class ObserverAndSetup(BaselineOutputs):
    def test_observer_record_is_required_and_read(self):
        f = self.files({"valid": True, "workload": {"ok": True}})
        f.pop("q4-out/quota-watch.jsonl")
        self.assertTrue(any("did not run" in p for p in qb.check_outputs(self.S, _tgz(f))[1]))
        f = self.files({"valid": True, "workload": {"ok": True}})
        f["q4-out/quota-watch.jsonl"] = b'{"ts": 1, "event": "started"}\n{"ts": 2, "event": "quota", "message": "rate limit"}\n{"ts": 9, "event": "stopped"}\n'
        self.assertTrue(any("observer" in p for p in qb.check_outputs(self.S, _tgz(f))[1]))

    def test_setup_goes_on_only_for_workload_problems(self):
        self.assertTrue(qb.setup_can_continue("setup", ["session not valid: workload ..."]))
        self.assertFalse(qb.setup_can_continue("setup", ["session not valid: x", "quota or rate limit reached (observer): 'x'"]))
        self.assertFalse(qb.setup_can_continue("dev", ["session not valid: workload ..."]), "never outside the set-up")
        self.assertFalse(qb.setup_can_continue("setup", []))
