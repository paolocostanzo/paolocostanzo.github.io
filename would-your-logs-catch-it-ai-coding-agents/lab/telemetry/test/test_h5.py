"""h5.py: the D27 point 4 verdict from score.py's output. Run: python3 -m unittest discover -s telemetry/test"""
import importlib.util, unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("h5", Path(__file__).resolve().parent.parent / "rules/h5.py")
h5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h5)


def s(scored, detected, windows, fp, tells):
    return {"events": {"scored": scored, "detected": detected}, "holdout": {"windows": windows, "fp_windows": fp},
            "tells_apart": tells}


class H5(unittest.TestCase):
    def test_one_failing_measurable_pair_makes_the_type_not_distinguishable(self):
        v = h5.verdict({"a/x": s(6, 6, 60, 0, True), "b/x": s(6, 5, 60, 0, False), "a/y": s(6, 6, 60, 1, True)})
        self.assertFalse(v["event_types"]["x"]["distinguishable"])
        self.assertTrue(v["event_types"]["y"]["distinguishable"])
        self.assertTrue(v["h5_holds"])

    def test_not_measurable_pairs_are_listed_and_left_out(self):
        v = h5.verdict({"a/x": s(6, 6, 60, 0, True), "b/x": s(0, 0, 60, 0, False)})
        self.assertEqual(v["not_measurable"], ["b/x"])
        self.assertTrue(v["event_types"]["x"]["distinguishable"])
        self.assertFalse(v["h5_holds"])

    def test_no_holdout_windows_cannot_pass(self):
        v = h5.verdict({"a/x": s(6, 6, 0, 0, True)})
        self.assertFalse(v["event_types"]["x"]["distinguishable"])
        self.assertTrue(v["h5_holds"])

    def test_incomplete_clients_get_a_verdict_with_and_without_them(self):
        v = h5.verdict({"a/x": s(6, 6, 60, 0, True), "b/x": s(6, 6, 0, 0, False)}, incomplete=["b"])
        self.assertTrue(v["all_clients"]["h5_holds"])
        self.assertFalse(v["without_incomplete_clients"]["h5_holds"])
        self.assertTrue(v["pairs"]["b/x"]["preliminary"])

    def test_host_rules_are_refused(self):
        with self.assertRaises(SystemExit):
            h5.verdict({"host/x": s(6, 6, 60, 0, True)})


if __name__ == "__main__":
    unittest.main()
