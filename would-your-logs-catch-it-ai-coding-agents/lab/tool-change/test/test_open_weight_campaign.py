"""Campaign guards of the open-weight cells (D39): a resume must be the same campaign, and the analysis follows the plan.
No VM is touched: every case stops before the first restore."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import analyze_open_weight as aow  # noqa: E402
import q3_open_weight_host as host  # noqa: E402

ARGS = ["--snapshot", "s1", "--clients", "qwen", "--signals", "none", "--seed", "1"]


class CampaignTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.out = self.root / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, extra=()):
        with mock.patch.object(host, "STATE", self.state), mock.patch.object(host, "run_cells") as rc, \
                mock.patch.object(host.qh, "run"), mock.patch.object(sys, "argv", ["x", *ARGS, "--out", str(self.out), *extra]):
            host.main()
        return rc

    def test_first_run_writes_the_plan_with_inputs(self):
        self.run_main()
        plan = json.loads((self.out / "plan.json").read_text())
        self.assertEqual((plan["seed"], plan["rep"], plan["snapshot"]), (1, 1, "s1"))
        self.assertIn("record_proxy.py", plan["inputs_sha256"])

    def test_resume_with_other_arguments_is_refused_before_any_cell(self):
        self.run_main()
        with self.assertRaises(SystemExit):
            rc = self.run_main(["--rep", "2"])
            rc.assert_not_called()

    def test_same_campaign_resumes(self):
        self.run_main()
        self.run_main().assert_called_once()

    def test_pending_evidence_of_another_campaign_blocks(self):
        self.state.mkdir()
        (self.state / "pending.json").write_text("{}")
        with self.assertRaises(SystemExit):
            self.run_main()
        self.assertFalse((self.out / "plan.json").exists())

    def test_analysis_reports_missing_and_unexpected_cells(self):
        self.out.mkdir()
        (self.out / "plan.json").write_text(json.dumps({"rep": 1, "cells": [{"client": "qwen", "signal": "none"},
                                                                             {"client": "goose", "signal": "none"}]}))
        (self.out / "codex__none__description__r1").mkdir()
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["x", str(self.out)]), redirect_stdout(buf):
            aow.main()
        d = json.loads(buf.getvalue())
        self.assertEqual(d["cells"], [])
        self.assertEqual(sorted(d["missing"]), ["goose__none__description__r1", "qwen__none__description__r1"])
        self.assertEqual(d["unexpected"], ["codex__none__description__r1"])


if __name__ == "__main__":
    unittest.main()
