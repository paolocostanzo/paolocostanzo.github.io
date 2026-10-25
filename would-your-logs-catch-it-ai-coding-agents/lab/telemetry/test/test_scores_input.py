"""scores_input.py: absent or unreadable evidence is listed as missing, never an exception (Codex review of the freeze, FREEZE-02)."""
import importlib.util, json, tempfile, unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("si", Path(__file__).resolve().parent.parent / "rules/scores_input.py")
si = importlib.util.module_from_spec(spec)
spec.loader.exec_module(si)


class Missing(unittest.TestCase):
    def test_unreadable_archives_and_absent_folders_are_missing(self):
        root = Path(tempfile.mkdtemp())
        ev, base = root / "events", root / "base"
        for d in (ev, base):
            d.mkdir()
        (ev / "plan.json").write_text(json.dumps({"runs": [{"client": "codex", "event": "q5-start", "name": "codex__q5-start__r1", "rep": 1},
                                                           {"client": "qwen", "event": "q5-start", "name": "qwen__q5-start__r1", "rep": 1}]}))
        (base / "plan.json").write_text(json.dumps({"runs": [{"client": "codex", "part": "holdout", "name": "baseline__codex__holdout"}]}))
        r = ev / "codex__q5-start__r1"
        r.mkdir()
        (r / "run.json").write_text(json.dumps({"client": "codex", "event": "q5-start", "export": "ok", "t0": 0, "t1": 1}))
        (r / "vm-out.tgz").write_text("not an archive")
        h = base / "baseline__codex__holdout"
        h.mkdir()
        (h / "run.json").write_text("{}")
        rules = {"codex/q5-start": {"client": "codex", "event": "q5-start", "type": "none", "reason": "x"},
                 "qwen/q5-start": {"client": "qwen", "event": "q5-start", "type": "none", "reason": "x"}}
        out = si.build(rules, ev, base)
        self.assertEqual(sorted(m["name"] for m in out["missing"]),
                         ["baseline__codex__holdout", "codex__q5-start__r1", "qwen__q5-start__r1"])
        self.assertEqual(out["incomplete_clients"], ["codex", "qwen"])


    def test_valid_archives_without_the_needed_members_are_missing(self):
        import io, tarfile
        root = Path(tempfile.mkdtemp())

        def tgz(path, members):
            with tarfile.open(path, "w:gz") as t:
                for name, data in members.items():
                    b = data.encode()
                    info = tarfile.TarInfo(name)
                    info.size = len(b)
                    t.addfile(info, io.BytesIO(b))
        d = root / "codex__q5-start__r1"
        d.mkdir()
        tgz(d / "vm-out.tgz", {"q3-server.log": ""})  # no canary-start.log
        self.assertFalse(si.ground_truth_ok(d, {"event": "q5-start"}))
        ok = {"clients": {"codex": []}, "patterns": {"codex": [{"pattern": ".codex/*.jsonl", "location": "absent"}]}}
        (d / "collection.json").write_text(json.dumps({"client": "codex", "export": "ok"}))
        cases = {"no manifest": {"x": ""}, "empty manifest": {"manifest.json": "{}"},
                 "another client only": {"manifest.json": json.dumps({"clients": {"qwen": []}, "patterns": {"qwen": []}})},
                 "listed file absent": {"manifest.json": json.dumps({**ok, "clients": {"codex": [{"path": ".codex/l.jsonl"}]}})}}
        for why, members in cases.items():
            tgz(d / "client-logs.redacted.tgz", members)
            self.assertFalse(si.client_archive_ok(d, "codex"), why)
        tgz(d / "client-logs.redacted.tgz", {"manifest.json": json.dumps(ok)})  # explicit no-default-logs: a real observation
        self.assertTrue(si.client_archive_ok(d, "codex"))
        (d / "collection.json").write_text(json.dumps({"client": "codex", "export": "failed"}))
        self.assertFalse(si.client_archive_ok(d, "codex"), "the runner's collection check failed")


if __name__ == "__main__":
    unittest.main()
