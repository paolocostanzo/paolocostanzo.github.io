"""compare_census.py on synthetic readings: join, missing/duplicate tools, kappa inputs, untouched second labels, adjudication list.
Run: python3 -m unittest discover -s defence-coverage/test"""
import csv, importlib.util, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("compare_census", HERE.parent / "scripts/compare_census.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


def write(rows, fields):
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    f.close()
    return f.name


FIRST = ["server", "tool", "class", "protection", "contested"]
SECOND = ["server", "tool", "class", "protection", "vendor_only", "evidence", "valid"]


class Compare(unittest.TestCase):
    def setUp(self):
        self.first = cc.read(write([
            {"server": "s", "tool": "a", "class": "A", "protection": "full", "contested": "False"},
            {"server": "s", "tool": "b", "class": "A", "protection": "none", "contested": "False"},
            {"server": "s", "tool": "c", "class": "B", "protection": "n-a", "contested": "False"},
            {"server": "s", "tool": "d", "class": "A", "protection": "full", "contested": "True"},
            {"server": "s", "tool": "only-first", "class": "B", "protection": "n-a", "contested": "False"},
            {"server": "s", "tool": "e", "class": "C", "protection": "n-a", "contested": "False"},
            {"server": "s", "tool": "f", "class": "A", "protection": "full", "contested": "False"},
        ], FIRST))
        self.second = cc.read(write([
            {"server": "s", "tool": "a", "class": "A", "protection": "full", "vendor_only": "false", "evidence": "x:1", "valid": "True"},
            {"server": "s", "tool": "b", "class": "A", "protection": "partial", "vendor_only": "false", "evidence": "x:2", "valid": "True"},
            {"server": "s", "tool": "c", "class": "B", "protection": "n-a", "vendor_only": "false", "evidence": "x:3", "valid": "True"},
            {"server": "s", "tool": "d", "class": "A", "protection": "full", "vendor_only": "true", "evidence": "x:4", "valid": "True"},
            {"server": "s", "tool": "e", "class": "B", "protection": "n-a", "vendor_only": "false", "evidence": "x:6", "valid": "True"},
            {"server": "s", "tool": "f", "class": "A", "protection": "full", "vendor_only": "true", "evidence": "x:7", "valid": "True"},
            {"server": "s", "tool": "only-second", "class": "A", "protection": "none", "vendor_only": "false", "evidence": "x:5", "valid": "True"},
            {"server": "s", "tool": "from-invalid", "class": "A", "protection": "none", "vendor_only": "false", "evidence": "", "valid": "False"},
        ], SECOND), valid_only=True)

    def test_join_and_missing(self):
        r = cc.compare(self.first, self.second)
        self.assertEqual(r["n_joined"], 6)
        self.assertEqual(r["missing_in_second"], [["s", "only-first"]])
        self.assertEqual(r["missing_in_first"], [["s", "only-second"]])
        self.assertNotIn(["s", "from-invalid"], r["missing_in_first"], "rows from invalid sessions are dropped")

    def test_agreement_uses_untouched_second_labels(self):
        r = cc.compare(self.first, self.second)
        self.assertEqual(r["class"]["raw_agreement"], 1.0, "C in the first reading and B in the second are both not-A")
        self.assertAlmostEqual(r["protection"]["raw_agreement"], 5 / 6)
        self.assertAlmostEqual(r["vendor_only"]["raw_agreement"], 5 / 6)

    def test_adjudication_lists_class_a_disagreements_only(self):
        r = cc.compare(self.first, self.second)
        self.assertEqual([(x["server"], x["tool"]) for x in r["to_adjudicate"]], [("s", "b"), ("s", "f")],
                         "a vendor-only disagreement on a class-A tool is adjudicated too")
        self.assertEqual(r["to_adjudicate"][0]["second"]["evidence"], "x:2")

    def test_duplicates_are_reported_and_left_out(self):
        dup = cc.read(write([
            {"server": "s", "tool": "a", "class": "A", "protection": "full", "vendor_only": "false", "evidence": "", "valid": "True"},
            {"server": "s", "tool": "a", "class": "A", "protection": "none", "vendor_only": "false", "evidence": "", "valid": "True"},
        ], SECOND), valid_only=True)
        r = cc.compare(self.first, dup)
        self.assertEqual(r["duplicates_second"], [["s", "a"]])
        self.assertEqual(r["n_joined"], 0)


class SecondLabelsUntouched(unittest.TestCase):
    def test_second_protection_is_not_rewritten(self):
        first = cc.read(write([{"server": "s", "tool": "x", "class": "B", "protection": "n-a", "contested": "False"}], FIRST))
        second = cc.read(write([{"server": "s", "tool": "x", "class": "B", "protection": "full", "vendor_only": "false",
                                 "evidence": "", "valid": "True"}], SECOND), valid_only=True)
        r = cc.compare(first, second)
        self.assertEqual(r["protection"]["raw_agreement"], 0.0, "B/full in the second reading is a disagreement, not n-a")

    def test_inconsistent_row_stays_and_goes_to_adjudication(self):
        first = cc.read(write([{"server": "s", "tool": "x", "class": "A", "protection": "full", "contested": "False"}], FIRST))
        second = cc.read(write([{"server": "s", "tool": "x", "class": "B", "protection": "full", "vendor_only": "false",
                                 "evidence": "e", "valid": "True", "consistent": "False"}], SECOND + ["consistent"]), valid_only=True)
        r = cc.compare(first, second)
        self.assertEqual(r["n_joined"], 1, "the row is not dropped as missing")
        self.assertEqual(r["class"]["raw_agreement"], 0.0)
        self.assertEqual([(x["tool"], x["second"]["consistent"]) for x in r["to_adjudicate"]], [("x", False)])
        self.assertEqual(r["second_inconsistent_rows"], [["s", "x"]])


class Validity(unittest.TestCase):
    def test_second_reading_without_valid_column_is_refused(self):
        path = write([{"server": "s", "tool": "a", "class": "A", "protection": "full", "vendor_only": "false", "evidence": ""}],
                     ["server", "tool", "class", "protection", "vendor_only", "evidence"])
        with self.assertRaises(SystemExit):
            cc.read(path, valid_only=True)

    def test_only_explicit_true_counts(self):
        path = write([{"server": "s", "tool": t, "class": "A", "protection": "full", "vendor_only": "false", "evidence": "", "valid": v}
                      for t, v in (("a", "True"), ("b", ""), ("c", "yes"), ("d", "false"))], SECOND)
        self.assertEqual(sorted(k[1] for k in cc.read(path, valid_only=True)), ["a"])


if __name__ == "__main__":
    unittest.main()
