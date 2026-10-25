"""vm_logs.py and collection_check.py on a fake home (D24 points 1-2). Run: python3 -m unittest discover -s telemetry/test"""
import hashlib, importlib.util, io, json, os, shutil, sqlite3, subprocess, sys, tarfile, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cc = load("collection_check")
CANDIDATES = {"_note": "x", "codex": ["~/.codex/sessions/**/*.jsonl", "~/.codex/log/**", "~/.codex/logs_*.sqlite"],
              "gemini": ["~/.gemini/tmp/**/logs.json"], "goose": ["~/.local/state/goose/logs/**"]}
CAND_BYTES = json.dumps(CANDIDATES).encode()
CAND_SHA = hashlib.sha256(CAND_BYTES).hexdigest()


def run_vm(home, *args):
    env = dict(os.environ, HOME=str(home))
    cand = home / "candidates.json"
    cand.write_bytes(CAND_BYTES)
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / "vm_logs.py"), args[0], str(cand), *args[1:]], env=env,
                          capture_output=True, check=True).stdout


def archive(manifest, files=()):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in [("manifest.json", json.dumps(manifest).encode()), *files]:
            info = tarfile.TarInfo(name); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Collection(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        old = self.home / ".codex/log/codex-tui.log"
        old.parent.mkdir(parents=True)
        old.write_text("old line\n")
        os.utime(old, (1000, 1000))
        self.start, self.end = int(time.time()) - 60, int(time.time()) + 1
        new = self.home / ".codex/sessions/2026/10/17/rollout-x.jsonl"
        new.parent.mkdir(parents=True)
        new.write_text(json.dumps({"type": "tool_call", "timestamp": self.start + 10}) + "\n")
        self.inv = json.loads(run_vm(self.home, "inventory"))

    def collect(self, start=None, end=None):
        arc = self.home / "s.tgz"
        arc.write_bytes(run_vm(self.home, "collect", str(start or self.start), str(end or self.end)))
        return arc

    def test_inventory_lists_only_existing_files(self):
        self.assertEqual(sorted(f["path"] for f in self.inv["clients"]["codex"]["files"]),
                         [".codex/log/codex-tui.log", ".codex/sessions/2026/10/17/rollout-x.jsonl"])
        self.assertEqual(self.inv["clients"]["gemini"]["files"], [])
        self.assertNotIn("_note", self.inv["clients"])

    def test_collect_and_check_ok(self):
        r = cc.check(str(self.collect()), "codex", self.start, self.end, self.inv, CAND_SHA)
        self.assertEqual(r["export"], "ok", r.get("reason"))
        self.assertEqual(r["client_logs"], "wrote")
        self.assertEqual(r["files_modified_in_session"], 1, "the old log was not modified in the session")
        self.assertEqual(r["record_coverage"]["files_with_records_in_session"], 1)
        self.assertEqual([p["matches"] for p in r["patterns"]], [1, 1, 0], "every frozen pattern is accounted for, empty ones too")
        self.assertIn("settings_sha256", r["logging_config"])
        pub = cc.public_record(r)
        self.assertNotIn("rollout-x", json.dumps(pub), "the public record names no file")
        self.assertEqual([(p["files"], p["with_records_in_session"]) for p in pub["patterns"]], [(1, 1), (1, 0), (0, 0)])

    def test_negative_observation_and_no_default_logs_are_distinguished(self):
        arc = self.collect()
        self.assertEqual(cc.check(str(arc), "gemini", self.start, self.end, self.inv, CAND_SHA)["client_logs"], "no-default-logs")
        later = cc.check(str(arc), "codex", self.end + 3600, self.end + 7200, self.inv, CAND_SHA)
        self.assertEqual(later["export"], "ok")
        self.assertEqual(later["client_logs"], "wrote-nothing-in-session", "a negative observation, not an export failure")

    def test_failed_export_is_detected(self):
        bad = self.home / "bad.tgz"
        bad.write_bytes(b"not a tar")
        self.assertEqual(cc.check(str(bad), "codex", self.start, self.end, self.inv, CAND_SHA)["export"], "failed")
        ok_patterns = {"codex": [{"pattern": p, "matches": 0, "location": "absent"} for p in CANDIDATES["codex"]]}
        cases = {
            "no entry for codex": {"candidates_sha256": CAND_SHA, "clients": {}, "patterns": {}},
            "does not account": {"candidates_sha256": CAND_SHA, "clients": {"codex": []}, "patterns": {"codex": ok_patterns["codex"][:1]}},
            "another candidates file": {"candidates_sha256": "0" * 64, "clients": {"codex": []}, "patterns": ok_patterns},
            "missing from archive": {"candidates_sha256": CAND_SHA, "patterns": ok_patterns,
                                     "clients": {"codex": [{"path": ".codex/x.jsonl", "size": 1, "mtime": 1, "sha256": "0" * 64}]}},
            "not readable in the VM": {"candidates_sha256": CAND_SHA, "clients": {"codex": []}, "patterns": ok_patterns,
                                       "errors": [{"client": "codex", "path": ".codex/y", "error": "EACCES"}]},
        }
        for reason, manifest in cases.items():
            p = self.home / "m.tgz"
            p.write_bytes(archive(manifest))
            r = cc.check(str(p), "codex", self.start, self.end, self.inv, CAND_SHA)
            self.assertEqual(r["export"], "failed", reason)
            self.assertIn(reason, r["reason"])

    def test_unreadable_location_is_a_failed_export_not_an_empty_one(self):
        if os.geteuid() == 0:
            self.skipTest("root reads everything")
        locked = self.home / ".gemini/tmp/secret"
        locked.mkdir(parents=True)
        (locked / "logs.json").write_text("[]")
        os.chmod(locked, 0)
        try:
            arc = self.collect()
        finally:
            os.chmod(locked, 0o755)
        r = cc.check(str(arc), "gemini", self.start, self.end, self.inv, CAND_SHA)
        self.assertEqual(r["export"], "failed")
        self.assertIn("not readable", r["reason"])
        self.assertEqual(cc.public_record(r)["reason"], "files not readable in the VM", "the public reason carries no path")
        absent = cc.check(str(arc), "goose", self.start, self.end, self.inv, CAND_SHA)
        self.assertEqual((absent["export"], absent["client_logs"]), ("ok", "no-default-logs"))
        self.assertEqual(absent["patterns"][0]["location"], "absent")

    def test_sqlite_records_still_in_the_wal_are_exported(self):
        db = self.home / ".codex/logs_2.sqlite"
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL"); con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("CREATE TABLE logs (ts INTEGER, msg TEXT)")
        con.executemany("INSERT INTO logs VALUES (?, ?)", [(self.start + 5, "a"), (self.start + 6, "b")])
        con.commit()  # committed, but only in the -wal file while this connection stays open
        try:
            self.assertTrue((self.home / ".codex/logs_2.sqlite-wal").exists())
            only_main = self.home / "main-only.sqlite"
            shutil.copyfile(db, only_main)
            n = sqlite3.connect(only_main).execute("SELECT count(*) FROM sqlite_master WHERE name='logs'").fetchone()[0]
            self.assertEqual(n, 0, "the main file alone does not hold the records")
            arc = self.collect()
        finally:
            con.close()
        r = cc.check(str(arc), "codex", self.start, self.end, self.inv, CAND_SHA)
        self.assertEqual(r["export"], "ok", r.get("reason"))
        tar = tarfile.open(arc, "r:gz")
        man = json.load(tar.extractfile("manifest.json"))
        self.assertEqual(man["sqlite"][".codex/logs_2.sqlite"]["tables"], {"logs": 2})
        self.assertIn("home/.codex/logs_2.sqlite-wal", tar.getnames())
        out = self.home / "cons.sqlite"
        out.write_bytes(tar.extractfile("sqlite-consolidated/.codex/logs_2.sqlite").read())
        self.assertEqual(sqlite3.connect(out).execute("SELECT count(*) FROM logs").fetchone()[0], 2)
        self.assertEqual(r["record_coverage"]["per_file"][".codex/logs_2.sqlite"], True)


if __name__ == "__main__":
    unittest.main()


class Edges(unittest.TestCase):
    def test_unsearchable_ancestor_is_not_absent(self):
        if os.geteuid() == 0:
            self.skipTest("root searches everything")
        vm = load("vm_logs")
        home = Path(tempfile.mkdtemp())
        (home / ".gemini/tmp/x").mkdir(parents=True)
        vm.HOME = str(home)
        os.chmod(home / ".gemini", 0)
        old = os.environ.get("HOME"); os.environ["HOME"] = str(home)
        try:
            loc = vm.inspect_location("~/.gemini/tmp/**/logs.json")
            gone = vm.inspect_location("~/.nothing/here/**")
        finally:
            os.chmod(home / ".gemini", 0o755)
            os.environ["HOME"] = old
        self.assertEqual(loc["location"], "present")
        self.assertTrue(loc["errors"], "an ancestor that cannot be searched is an inspection failure")
        self.assertEqual(gone, {"location": "absent", "errors": []})

    def test_plain_text_logs_are_counted_as_without_timestamps(self):
        home = Path(tempfile.mkdtemp())
        log = home / ".local/state/goose/logs/cli/2026-10-18/a.log"
        log.parent.mkdir(parents=True)
        log.write_text("plain text\n")
        start, end = int(time.time()) - 60, int(time.time()) + 1
        inv = json.loads(run_vm(home, "inventory"))
        arc = home / "s.tgz"
        arc.write_bytes(run_vm(home, "collect", str(start), str(end)))
        r = cc.check(str(arc), "goose", start, end, inv, CAND_SHA)
        self.assertEqual(r["export"], "ok", r.get("reason"))
        pub = cc.public_record(r)
        self.assertEqual(pub["patterns"][0]["files"], 1)
        self.assertEqual(pub["patterns"][0]["without_timestamps"], 1)


class StopClients(unittest.TestCase):
    """stop_clients.sh kills processes: these tests NEVER let it reach the real pkill, pgrep, tmux, ps, hostname or id. They run
    it with a PATH that holds only stubs for those (which record their arguments and answer as told) plus a few harmless tools,
    so the real ones are unreachable; the guard and every failure path are checked with stubs only."""
    REAL = ["bash", "seq", "sleep", "tr", "cat"]
    STUBBED = ["pkill", "pgrep", "tmux", "ps", "hostname", "id"]

    def stub_path(self, host="agentlab", host_rc=0, user="lab", pgrep="none", alive_rounds=0):
        d = Path(tempfile.mkdtemp())
        log = d / "calls.log"
        rec = lambda name: f'echo "{name} $*" >> "{log}"'  # the name is fixed: basename is not on the restricted PATH
        for name in ("pkill", "tmux", "ps"):
            (d / name).write_text(f"#!/bin/sh\n{rec(name)}\n")
        (d / "hostname").write_text(f"#!/bin/sh\n{rec('hostname')}\n[ {host_rc} -eq 0 ] && echo {host}\nexit {host_rc}\n")
        (d / "id").write_text(f'#!/bin/sh\n{rec("id")}\n[ "$1" = "-un" ] && echo {user} || echo 1001\n')
        if pgrep == "error":
            body = "exit 2"
        else:  # report a survivor for the first alive_rounds calls (-1 = always), then nothing
            body = (f'n=$(cat "{d}/n" 2>/dev/null || echo 0); echo $((n+1)) > "{d}/n"\n'
                    f'if [ {alive_rounds} -lt 0 ] || [ "$n" -lt {alive_rounds} ]; then echo 4242; exit 0; fi\nexit 1')
        (d / "pgrep").write_text(f"#!/bin/sh\n{rec('pgrep')}\n{body}\n")
        for name in self.STUBBED:
            os.chmod(d / name, 0o755)
        for name in self.REAL:
            real = shutil.which(name)
            if real is None:
                self.skipTest(f"{name} not found")
            os.symlink(real, d / name)
        for name in self.STUBBED:
            self.assertEqual(shutil.which(name, path=str(d)), str(d / name), f"{name} must resolve to the stub")
        self.assertIsNone(shutil.which("kill", path=str(d)))
        return d, log

    def run_script(self, d, home, flag=True):
        env = {"PATH": str(d), "HOME": str(home), **({"Q4_IN_VM": "1"} if flag else {})}  # only the stub directory
        return subprocess.run([str(d / "bash"), str(SCRIPTS / "stop_clients.sh")], capture_output=True, text=True, env=env, timeout=60)

    def vm_home(self, marker=True):
        home = Path(tempfile.mkdtemp())
        if marker:
            (home / ".agentlab_env").write_text("")
        return home

    def killed(self, log):
        return log.exists() and any(l.split()[0] in ("pkill", "tmux") for l in log.read_text().splitlines())

    def test_script_calls_tools_by_name_only(self):
        text = (SCRIPTS / "stop_clients.sh").read_text()
        self.assertNotRegex(text, r"/(usr/)?s?bin/(pkill|pgrep|tmux|kill)\b", "absolute paths would bypass the stubs")

    def test_refuses_outside_the_vm(self):
        cases = {"no flag": ({}, False, True), "no marker": ({}, True, False), "another user": ({"user": "someone"}, True, True),
                 "hostname fails": ({"host_rc": 1}, True, True), "the lab host": ({"host": "parrot"}, True, True)}
        for why, (stub, flag, marker) in cases.items():
            d, log = self.stub_path(**stub)
            r = self.run_script(d, self.vm_home(marker), flag=flag)
            self.assertEqual(r.returncode, 3, f"{why}: {r.stdout}{r.stderr}")
            self.assertIn("refusing to run", r.stderr, why)
            self.assertFalse(self.killed(log), f"{why}: nothing may be killed")

    def test_inside_the_vm_stops_and_waits(self):
        d, log = self.stub_path(alive_rounds=8)  # survivors for a while, then gone
        r = self.run_script(d, self.vm_home())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("clients stopped", r.stdout)
        calls = log.read_text()
        self.assertIn("tmux kill-server", calls)
        self.assertIn("pkill -TERM -u 1001 -f [c]laude", calls)
        self.assertNotIn("-KILL", calls)

    def test_survivors_are_killed_and_reported(self):
        d, log = self.stub_path(alive_rounds=-1)
        r = self.run_script(d, self.vm_home())
        self.assertEqual(r.returncode, 1)
        self.assertIn("still running", r.stdout)
        self.assertIn("pkill -KILL", log.read_text())

    def test_enumeration_error_fails_closed(self):
        d, log = self.stub_path(pgrep="error")
        r = self.run_script(d, self.vm_home())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("cannot tell", r.stdout)
