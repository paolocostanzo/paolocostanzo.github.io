"""final_census.py on synthetic readings: agreed and adjudicated labels, missing decisions, the server allowlist, D23.
Run: python3 -m unittest discover -s defence-coverage/test"""
import csv, json, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "final_census.py"
F_HDR = ["repo", "commit", "server", "tool", "class", "protection", "contested", "correction", "mechanism", "evidence"]
S_HDR = ["server", "tool", "class", "protection", "vendor_only", "valid", "evidence"]
A_HDR = ["n", "server", "tool", "first_class", "first_protection", "first_vendor_only", "second_class", "second_protection",
         "second_vendor_only", "decision_class", "decision_protection", "decision_vendor_only", "note"]


def write(path, hdr, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=hdr, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


class FinalCensus(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        first = [  # server s1: a1 agreed full, a2 disagreement (decided partial), b1 not-A both; server hidden: one A tool
            {"server": "s1", "tool": "a1", "class": "A", "protection": "full", "contested": "False"},
            {"server": "s1", "tool": "a2", "class": "A", "protection": "full", "contested": "False"},
            {"server": "s1", "tool": "b1", "class": "B", "protection": "n-a", "contested": "False"},
            {"server": "s2", "tool": "x", "class": "A", "protection": "none", "contested": "True"},
            {"server": "hidden", "tool": "h", "class": "A", "protection": "none", "contested": "False"}]
        second = [
            {"server": "s1", "tool": "a1", "class": "A", "protection": "full", "vendor_only": "false", "valid": "True"},
            {"server": "s1", "tool": "a2", "class": "A", "protection": "none", "vendor_only": "false", "valid": "True"},
            {"server": "s1", "tool": "b1", "class": "B", "protection": "n-a", "vendor_only": "false", "valid": "True"},
            {"server": "s2", "tool": "x", "class": "A", "protection": "none", "vendor_only": "true", "valid": "True"},
            {"server": "hidden", "tool": "h", "class": "A", "protection": "full", "vendor_only": "false", "valid": "True"}]
        write(self.d / "first.csv", F_HDR, first)
        write(self.d / "second.csv", S_HDR, second)
        self.adj = [{"n": 1, "server": "s1", "tool": "a2", "decision_class": "A", "decision_protection": "partial", "decision_vendor_only": "false"},
                    {"n": 2, "server": "hidden", "tool": "h", "decision_class": "", "decision_protection": "", "decision_vendor_only": ""}]

    def run_(self, adj, *extra):
        write(self.d / "adj.csv", A_HDR, adj)
        return subprocess.run([sys.executable, "-I", str(SCRIPT), "--first", str(self.d / "first.csv"), "--second", str(self.d / "second.csv"),
                               "--adjudication", str(self.d / "adj.csv"), "--out", str(self.d / "out"), *extra],
                              capture_output=True, text=True)

    def test_allowlist_agreed_and_adjudicated(self):
        r = self.run_(self.adj, "--servers", "s1,s2")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = list(csv.DictReader(open(self.d / "out/final-census.csv")))
        self.assertEqual({x["server"] for x in rows}, {"s1", "s2"}, "the hidden server never reaches the outputs")
        self.assertNotIn("hidden", (self.d / "out/coverage.json").read_text() + (self.d / "out/COVERAGE.md").read_text())
        lab = {(x["server"], x["tool"]): (x["class"], x["protection"], x["source"]) for x in rows}
        self.assertEqual(lab[("s1", "a1")], ("A", "full", "agreed"))
        self.assertEqual(lab[("s1", "a2")], ("A", "partial", "adjudicated"))
        self.assertEqual(lab[("s1", "b1")], ("not-A", "n-a", "agreed"))
        cov = json.loads((self.d / "out/coverage.json").read_text())
        s1 = cov["all_A"]["per_server"]["s1"]
        self.assertEqual((s1["A"], s1["full"], s1["partial"], s1["none"]), (2, 1, 1, 0))
        self.assertEqual(cov["all_A"]["summary"]["pooled_any"], [2, 3])
        self.assertEqual(cov["without_vendor_only"]["per_server"]["s2"]["A"], 0, "s2's only A tool is vendor-only")
        self.assertIsNone(cov["without_vendor_only"]["per_server"]["s2"]["cov_full"])

    def test_missing_decision_stops(self):
        r = self.run_([self.adj[1] | {"server": "s1", "tool": "a2"}], "--servers", "s1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("need a decision", r.stderr)

    def test_d23_server_leaves_coverage_but_stays_in_the_census(self):
        r = self.run_(self.adj, "--servers", "s1,s2", "--no-defence", "s2")
        self.assertEqual(r.returncode, 0, r.stderr)
        cov = json.loads((self.d / "out/coverage.json").read_text())
        self.assertNotIn("s2", cov["all_A"]["per_server"])
        self.assertIn("s2", {x["server"] for x in csv.DictReader(open(self.d / "out/final-census.csv"))})

    def test_invalid_decision_is_rejected(self):
        bad = [self.adj[0] | {"decision_protection": "n-a"}]
        r = self.run_(bad, "--servers", "s1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid decision", r.stderr)


if __name__ == "__main__":
    unittest.main()


class FinalCensusEdges(FinalCensus):
    def test_excluded_server_outside_the_list_is_never_written(self):
        r = self.run_(self.adj, "--servers", "s1", "--no-defence", "hidden")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.d / "out/coverage.json").read_text() + (self.d / "out/COVERAGE.md").read_text()
        self.assertNotIn("hidden", text)

    def test_both_agreement_sets(self):
        r = self.run_(self.adj, "--servers", "s1,s2", "--no-defence", "s2")
        self.assertEqual(r.returncode, 0, r.stderr)
        ag = json.loads((self.d / "out/coverage.json").read_text())["agreement_before_adjudication"]
        self.assertEqual(ag["listed_servers"]["servers"], ["s1", "s2"])
        self.assertEqual(ag["coverage_servers"]["servers"], ["s1"])
        self.assertEqual((ag["listed_servers"]["n_joined"], ag["coverage_servers"]["n_joined"]), (4, 3))

    def test_duplicates_stop(self):
        with open(self.d / "second.csv", "a", newline="") as fh:
            csv.DictWriter(fh, fieldnames=S_HDR, extrasaction="ignore").writerow(
                {"server": "s1", "tool": "a1", "class": "A", "protection": "none", "vendor_only": "false", "valid": "True"})
        r = self.run_(self.adj, "--servers", "s1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("duplicate rows", r.stderr)

    def test_duplicate_adjudication_rows_stop(self):
        r = self.run_(self.adj + [self.adj[0] | {"n": 3}], "--servers", "s1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("duplicate adjudication", r.stderr)

    def test_inconsistent_second_row_needs_a_decision(self):
        rows = list(csv.DictReader(open(self.d / "second.csv", newline="")))
        for x in rows:
            if x["tool"] == "b1":
                x["protection"] = "full"  # B with a protection: contradicts its own class
        write(self.d / "second.csv", S_HDR, rows)
        r = self.run_(self.adj, "--servers", "s1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("need a decision", r.stderr)

    def test_decision_for_an_agreed_tool_stops(self):
        stale = {"n": 9, "server": "s1", "tool": "a1", "decision_class": "A", "decision_protection": "none", "decision_vendor_only": "false"}
        r = self.run_(self.adj + [stale], "--servers", "s1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("agree on", r.stderr)


class FinalCensusSelection(FinalCensus):
    def test_unknown_or_empty_server_selection_stops(self):
        for sel in ("s1,misspelled", "nothing", ""):
            r = self.run_(self.adj, "--servers", sel)
            self.assertNotEqual(r.returncode, 0, sel)

    def test_server_without_qualifying_tools_is_explicit(self):
        rows = list(csv.DictReader(open(self.d / "first.csv", newline="")))
        rows.append({"server": "s3", "tool": "b", "class": "B", "protection": "n-a", "contested": "False"})
        write(self.d / "first.csv", F_HDR, rows)
        rows = list(csv.DictReader(open(self.d / "second.csv", newline="")))
        rows.append({"server": "s3", "tool": "b", "class": "B", "protection": "n-a", "vendor_only": "false", "valid": "True"})
        write(self.d / "second.csv", S_HDR, rows)
        r = self.run_(self.adj, "--servers", "s1,s3")
        self.assertEqual(r.returncode, 0, r.stderr)
        cov = json.loads((self.d / "out/coverage.json").read_text())["all_A"]
        self.assertEqual(cov["per_server"]["s3"]["A"], 0)
        self.assertIsNone(cov["per_server"]["s3"]["cov_full"])
        self.assertEqual(cov["summary"]["servers"], 1, "a server with no qualifying tool is not averaged in")
