"""rules/normalize.py and rules/apply.py on small synthetic archives (no VM, no lab data).
Run: python3 -m unittest discover -s telemetry/test"""
import importlib.util, io, json, sqlite3, tarfile, tempfile, unittest
from pathlib import Path

RULES = Path(__file__).resolve().parent.parent / "rules"
spec = importlib.util.spec_from_file_location("apply_rules", RULES / "apply.py")
ap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ap)
nz = ap.nz
T0 = 1792690000.0


def archive(files):
    """files: {path under home/ or full member name: (bytes, mtime)} -> path of a .tgz like client-logs.redacted.tgz"""
    path = Path(tempfile.mkdtemp()) / "client-logs.redacted.tgz"
    with tarfile.open(path, "w:gz") as tar:
        for name, (data, mtime) in files.items():
            info = tarfile.TarInfo(name if name.startswith(("home/", "sqlite-consolidated/")) else "home/" + name)
            info.size, info.mtime = len(data), int(mtime)
            tar.addfile(info, io.BytesIO(data))
    return path


def iso(t):
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def jl(*recs):
    return ("\n".join(json.dumps(r) for r in recs) + "\n").encode()


class Normalize(unittest.TestCase):
    def test_client_locations_times_and_fallbacks(self):
        a = archive({
            ".claude/projects/p/s.jsonl": (jl({"timestamp": iso(T0 + 1), "type": "x"}), T0 + 2),
            ".local/state/goose/logs/cli/d/1.log": (jl({"timestamp": iso(T0 + 3), "fields": {}}), T0 + 3),
            ".local/state/goose/logs/llm_request.0.jsonl": (b'{"input": {}}\n{"x": 1}\n', T0 + 9),
            ".codex/history.jsonl": (jl({"ts": T0}), T0),
        })
        claude = nz.records(a, "claude-code")
        self.assertEqual([(r["src"], r["t"]) for r in claude], [(".claude/projects/p/s.jsonl", T0 + 1)])
        goose = nz.records(a, "goose")
        log = [r for r in goose if r["src"].endswith("1.log")]
        self.assertAlmostEqual(log[0]["t"], T0 + 3, places=3, msg="a JSON line in a .log file gives its own time")
        req = [r for r in goose if "llm_request" in r["src"]]
        self.assertEqual(len(req), 2, "a file without timed records keeps its lines")
        self.assertTrue(all(r["t"] == T0 + 9 and r["time_from"] == "mtime" for r in req))

    def test_naive_iso_is_utc_and_codex_nanos(self):
        self.assertEqual(nz.to_epoch("2026-10-22 17:00:00"), nz.to_epoch("2026-10-22T17:00:00Z"))
        db = Path(tempfile.mkdtemp()) / "logs.sqlite"
        con = sqlite3.connect(db)
        con.execute("create table logs (id integer, ts integer, ts_nanos integer, feedback_log_body text)")
        con.execute("insert into logs values (1, ?, 600000000, 'x')", (int(T0),))
        con.commit(); con.close()
        a = archive({".codex/logs_2.sqlite": (db.read_bytes(), T0)})
        r = nz.records(a, "codex")
        self.assertAlmostEqual(r[0]["t"], int(T0) + 0.6, places=3)
        self.assertEqual(r[0]["src"], ".codex/logs_2.sqlite#logs")

    def test_ansi_prefixed_times(self):
        line = f"\x1b[2m{iso(T0 + 4)}\x1b[0m INFO started\n".encode()
        a = archive({".codex/app-server-daemon/daemon.stderr.log": (line, T0 + 99)})
        r = nz.records(a, "codex")
        self.assertAlmostEqual(r[0]["t"], T0 + 4, places=3)
        self.assertNotIn("time_from", r[0])

    def test_window(self):
        a = archive({".codex/history.jsonl": (jl({"ts": T0}, {"ts": T0 + 100}), T0)})
        self.assertEqual([r["t"] for r in nz.records(a, "codex", T0 + 50, T0 + 200)], [T0 + 100])


class Apply(unittest.TestCase):
    def rules(self, **kw):
        return {k: {"client": "claude-code", "event": "q3-silent", "class": "c", **v} for k, v in kw.items()}

    def tool(self, t, desc, kind="deferred"):
        if kind == "deferred":
            return {"timestamp": iso(t), "attachment": {"type": "deferred_tools_record",
                                                        "entries": [{"name": "mcp__srv__t", "description": desc, "input_schema": {}}]}}
        return {"timestamp": iso(t), "type": "api-request-shape", "shape": {"tools": [{"name": "mcp__srv__t", "description": desc}]}}

    def test_match_changed_unannounced_and_window(self):
        a = archive({
            ".claude/projects/p/a.jsonl": (jl(self.tool(T0 - 500, "old"), self.tool(T0 + 10, "old"), self.tool(T0 + 40, "new")), T0),
            ".cache/claude-cli-nodejs/p/mcp-logs-srv/1.jsonl": (jl({"debug": "Received tools/list_changed notification, x",
                                                                     "timestamp": iso(T0 + 20)}), T0),
        })
        rules = self.rules(
            ann={"type": "match", "source": ".cache/claude-cli-nodejs/*/mcp-logs-*/*.jsonl",
                 "regex": "Received tools/list_changed notification"},
            chg={"type": "changed", "source": ".claude/projects/*/*.jsonl", "extract": "claude-tools"},
            uns={"type": "unannounced", "changed": "chg", "announced": "ann"},
            no={"type": "none", "reason": "r"})
        h = ap.apply(rules, a, "claude-code", T0, T0 + 100)
        self.assertEqual(h["ann"], [T0 + 20])
        self.assertEqual(h["chg"], [T0 + 40])
        self.assertEqual(h["uns"], [], "the notification lies between the two observations")
        self.assertEqual(h["no"], [])
        self.assertEqual(ap.apply(rules, a, "claude-code", T0 + 50, T0 + 100)["chg"], [], "hits outside the window are not returned")

    def test_shape_and_deferred_are_separate_streams(self):
        a = archive({".claude/projects/p/a.jsonl": (jl(self.tool(T0, "full", "deferred"), self.tool(T0 + 5, "short", "shape")), T0)})
        rules = self.rules(chg={"type": "changed", "source": ".claude/projects/*/*.jsonl", "extract": "claude-tools"})
        self.assertEqual(ap.apply(rules, a, "claude-code", T0 - 10, T0 + 10)["chg"], [])

    def test_new_name_with_precursor_and_history(self):
        a = archive({".claude/projects/p/a.jsonl": (b"", T0),
                     ".cache/claude-cli-nodejs/p/mcp-logs-old/0.jsonl": (jl({"timestamp": iso(T0 - 900)}), T0),
                     ".cache/claude-cli-nodejs/p/mcp-logs-old/1.jsonl": (jl({"timestamp": iso(T0 + 5)}), T0),
                     ".cache/claude-cli-nodejs/p/mcp-logs-fresh/1.jsonl": (jl({"timestamp": iso(T0 + 6)}), T0)})
        rules = self.rules(new={"type": "new-name", "source": ".cache/claude-cli-nodejs/*/mcp-logs-*/*.jsonl", "field": "src",
                                "regex": "/mcp-logs-(?P<name>[^/]+)/"})
        self.assertEqual(ap.apply(rules, a, "claude-code", T0, T0 + 100)["new"], [T0 + 6], "a name seen in history does not fire")
        rules["new"].update({"after": "trust-write", "within_s": 10})
        self.assertEqual(ap.apply(rules, a, "claude-code", T0, T0 + 100)["new"], [], "no precursor, no hit")

    def test_mtime_records_only_when_allowed(self):
        d1 = json.dumps({"input": {"tools": {"functionDeclarations": [{"name": "x__t", "description": "a"}]}}}).encode()
        d2 = json.dumps({"input": {"tools": {"functionDeclarations": [{"name": "x__t", "description": "b"}]}}}).encode()
        a = archive({".local/state/goose/logs/llm_request.0.jsonl": (d1 + b"\n", T0 + 1),
                     ".local/state/goose/logs/llm_request.1.jsonl": (d2 + b"\n", T0 + 30)})
        base = {"client": "goose", "event": "q3-silent", "class": "c", "type": "changed",
                "source": ".local/state/goose/logs/llm_request*.jsonl", "extract": "goose-request-tools"}
        self.assertEqual(ap.apply({"g": base}, a, "goose", T0, T0 + 60)["g"], [])
        self.assertEqual(ap.apply({"g": {**base, "allow_mtime": True}}, a, "goose", T0, T0 + 60)["g"], [T0 + 30])

    def test_qwen_extractor_reads_only_tool_search_responses(self):
        def rec(t, name, desc):
            out = '<functions> <function>{"name":"mcp__srv__t","description":"%s"}</function></functions>' % desc
            return {"timestamp": iso(t), "type": "tool_result",
                    "message": {"parts": [{"functionResponse": {"name": name, "response": {"output": out}}}]}}
        a = archive({".qwen/projects/p/chats/a.jsonl": (jl(rec(T0, "tool_search", "v1"), rec(T0 + 5, "read_file", "v2"),
                                                          rec(T0 + 9, "tool_search", "v1")), T0)})
        rules = {"q": {"client": "qwen", "event": "q3-silent", "class": "c", "type": "changed",
                       "source": ".qwen/projects/*/chats/*.jsonl", "extract": "qwen-tool-search"}}
        self.assertEqual(ap.apply(rules, a, "qwen", T0 - 1, T0 + 20)["q"], [], "another tool's output is not a definition")
        a2 = archive({".qwen/projects/p/chats/a.jsonl": (jl(rec(T0, "tool_search", "v1"), rec(T0 + 9, "tool_search", "v2")), T0)})
        self.assertEqual(ap.apply(rules, a2, "qwen", T0 - 1, T0 + 20)["q"], [T0 + 9])

    def test_lab_literals_are_refused(self):
        for bad in ("q3_noop", "q3.lab", "q3 lab", "Lab tool, revision B", "q5-canary"):
            with self.assertRaises(SystemExit, msg=bad):
                ap.check_literals(self.rules(bad={"type": "match", "source": "*", "regex": bad}))
        with self.assertRaises(SystemExit):
            ap.check_literals(self.rules(bad={"type": "match", "source": "*", "regex": "x", "field": "q3-repo"}))

    def test_precursor_within_and_beyond(self):
        a = archive({".claude/projects/p/a.jsonl": (b"", T0),
                     ".cache/claude-cli-nodejs/p/mcp-logs-x/0.jsonl": (jl({"timestamp": iso(T0), "debug": "trust-write"}), T0),
                     ".cache/claude-cli-nodejs/p/mcp-logs-near/1.jsonl": (jl({"timestamp": iso(T0 + 5), "debug": "up"}), T0),
                     ".cache/claude-cli-nodejs/p/mcp-logs-far/1.jsonl": (jl({"timestamp": iso(T0 + 50), "debug": "up"}), T0)})
        rules = self.rules(new={"type": "new-name", "source": ".cache/claude-cli-nodejs/*/mcp-logs-*/*.jsonl", "field": "src",
                                "regex": "/mcp-logs-(?P<name>[^x/][^/]*)/", "after": "trust-write", "within_s": 10})
        self.assertEqual(ap.apply(rules, a, "claude-code", T0, T0 + 100)["new"], [T0 + 5])

    def test_changed_compares_within_a_project(self):
        a = archive({".claude/projects/one/a.jsonl": (jl(self.tool(T0, "v1")), T0),
                     ".claude/projects/two/b.jsonl": (jl(self.tool(T0 + 5, "v2")), T0),
                     ".claude/projects/one/c.jsonl": (jl(self.tool(T0 + 9, "v1")), T0)})
        rules = self.rules(chg={"type": "changed", "source": ".claude/projects/*/*.jsonl", "extract": "claude-tools"})
        self.assertEqual(ap.apply(rules, a, "claude-code", T0 - 1, T0 + 20)["chg"], [], "same name, different projects")

    def test_consolidated_copy_is_read_and_a_lone_wal_is_refused(self):
        def db(rows):
            path = Path(tempfile.mkdtemp()) / "x.sqlite"
            con = sqlite3.connect(path)
            con.execute("create table logs (id integer, ts integer)")
            con.executemany("insert into logs values (?, ?)", rows)
            con.commit(); con.close()
            return path.read_bytes()
        a = archive({".codex/logs_2.sqlite": (db([(1, int(T0))]), T0),
                     "sqlite-consolidated/.codex/logs_2.sqlite": (db([(1, int(T0)), (2, int(T0) + 7)]), T0),
                     ".codex/logs_2.sqlite-wal": (b"wal", T0)})
        self.assertEqual(len(nz.records(a, "codex")), 2, "the consolidated copy holds the rows still in the WAL")
        b = archive({".codex/logs_2.sqlite": (db([(1, int(T0))]), T0), ".codex/logs_2.sqlite-wal": (b"wal", T0)})
        with self.assertRaises(SystemExit):
            nz.records(b, "codex")

    def test_coverage_counts_windows_with_records(self):
        a = archive({".local/state/goose/logs/llm_request.0.jsonl": (b'{"input": {}}\n', T0 + 130)})
        rules = {"g": {"client": "goose", "event": "q3-silent", "class": "c", "type": "changed", "allow_mtime": True,
                       "source": ".local/state/goose/logs/llm_request*.jsonl", "extract": "goose-request-tools"},
                 "n": {"client": "goose", "event": "q5-start", "class": "-", "type": "none", "reason": "r"}}
        self.assertEqual(ap.coverage(rules, a, "goose", T0, T0 + 600), {"g": 1, "n": None})

    def test_candidate_file_is_well_formed(self):
        rules = json.load(open(RULES / "author-candidates.json"))["rules"]
        ap.check_literals(rules)
        for rid, r in rules.items():
            self.assertEqual(rid, f"{r['client']}/{r['event']}")
            self.assertIn(r["type"], ("match", "changed", "unannounced", "new-name", "none"))
            if r["type"] == "changed":
                self.assertIn(r["extract"], ap.EXTRACTORS)
        pairs = {(r["client"], r["event"]) for r in rules.values()}
        self.assertEqual(len(pairs), 15, "one rule per client and event type, five clients")


if __name__ == "__main__":
    unittest.main()
