"""score.py: windows, Wilson interval, true positives after the event, false-positive windows (D6, D13, D24 point 5)."""
import importlib.util, unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("score", Path(__file__).resolve().parent.parent / "scripts" / "score.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)
Z2 = 1.959964 ** 2


class Score(unittest.TestCase):
    def test_wilson(self):
        lo, hi = sc.wilson(0, 60)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, Z2 / (60 + Z2), places=9)  # closed form for k = 0
        a, b = sc.wilson(3, 60), sc.wilson(57, 60)
        self.assertAlmostEqual(a[0], 1 - b[1], places=12)  # symmetry
        self.assertTrue(a[0] < 3 / 60 < a[1])
        self.assertIsNone(sc.wilson(0, 0))

    def test_windows_drop_the_trailing_partial_window(self):
        self.assertEqual(sc.windows(100, 100 + 150), [(100, 160), (160, 220)])
        self.assertEqual(len(sc.windows(0, 3600)), 60)

    def test_event_hits_count_only_after_the_event(self):
        runs = [{"run": "a", "event_time": 50, "end": 200, "hits": [10, 120]},   # detected at 120 (10 is before)
                {"run": "b", "event_time": 50, "end": 200, "hits": [10]},        # missed: only a hit before the event
                {"run": "c", "event_time": 50, "end": 200, "hits": [], "status": "no-event"},
                {"run": "d", "event_time": 50, "end": 200, "hits": [], "status": "export-failed"}]
        r = sc.score_events(runs)
        self.assertEqual((r["scored"], r["detected"], r["missed"]), (2, 1, ["b"]))
        self.assertEqual(r["left_out"], {"no-event": 1, "export-failed": 1})
        self.assertEqual(r["latency_s"], {"min": 70, "max": 70})

    def test_holdout_and_threshold(self):
        hold = [{"session": "h", "t0": 0, "t1": 3600, "hits": [5, 6, 7, 3599.5, 4000]}]  # windows 0 and 59 fire; 4000 is outside
        h = sc.score_holdout(hold)
        self.assertEqual((h["windows"], h["fp_windows"]), (60, 2))
        self.assertAlmostEqual(h["hits_per_hour"], 4.0)
        ev = [{"run": "a", "event_time": 0, "end": 10, "hits": [1]}]
        self.assertTrue(sc.score({"r": {"event_runs": ev, "holdout": hold}})["r"]["tells_apart"])  # 2/60 = 3.3% < 5%
        edge = [{"session": "h", "t0": 0, "t1": 3600, "hits": [60 * i + 1 for i in range(3)]}]  # 3/60 = 5.0%: not below 5% (D27)
        self.assertFalse(sc.score({"r": {"event_runs": ev, "holdout": edge}})["r"]["tells_apart"])
        noisy = [{"session": "h", "t0": 0, "t1": 3600, "hits": [60 * i + 1 for i in range(4)]}]  # 4/60 = 6.7%
        self.assertFalse(sc.score({"r": {"event_runs": ev, "holdout": noisy}})["r"]["tells_apart"])
        self.assertFalse(sc.score({"r": {"event_runs": [], "holdout": hold}})["r"]["tells_apart"], "no scored run, no claim")


if __name__ == "__main__":
    unittest.main()
