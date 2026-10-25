"""secret_gate.py (D26): fingerprints, exact and format matching, archives, git ranges, failing closed, and the orchestrators' use
of it. Fake secrets are built at run time, so that this file itself contains nothing the gate would flag.
Run: python3 -m unittest discover -s telemetry/test"""
import gzip, importlib.util, io, json, os, secrets, subprocess, sys, tarfile, tempfile, unittest, zipfile
from pathlib import Path
from types import SimpleNamespace as _NS

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sg = _load("secret_gate")
q4 = _load("q4_events")
q4.STATE = Path(tempfile.mkdtemp())  # never the real ~/.agentlab-q4 of the host


def fake(kind):
    """A value with the shape of a real secret, assembled at run time."""
    r = secrets.token_urlsafe(48).replace("-", "a").replace("_", "b")
    return {"anthropic": "sk-" + "ant-" + "oat01-" + r,
            "google": "AI" + "za" + r[:35],
            "jwt": "ey" + "J" + r[:20] + ".ey" + "J" + r[20:40] + "." + r[40:60],
            "github": "gh" + "p_" + (r * 2)[:36],
            "opaque": secrets.token_hex(24)}[kind]


def full_fps(*extra):
    """A complete fingerprint set (every required kind, random values) plus the given fingerprints."""
    return [sg.fingerprint(fake("opaque"), k, "x") for k in sg.REQUIRED_KINDS] + list(extra)


def tgz(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Formats(unittest.TestCase):
    def kinds(self, text):
        return {k for k, _ in sg.scan_bytes(text.encode())}

    def test_each_known_format_is_found(self):
        self.assertIn("anthropic-token", self.kinds("x " + fake("anthropic") + " y"))
        self.assertIn("google-api-key", self.kinds("key=" + fake("google")))
        self.assertIn("jwt", self.kinds('{"a": "' + fake("jwt") + '"}'))
        self.assertIn("github-token", self.kinds(fake("github")))
        self.assertIn("private-key", self.kinds("-----BEGIN " + "OPENSSH PRIVATE KEY-----\nabc"))
        self.assertIn("token-field", self.kinds('{"refresh_' + 'token": "' + fake("opaque") + '"}'))
        self.assertIn("secret-env", self.kinds("export CLAUDE_CODE_OAUTH_" + "TOKEN=" + fake("opaque")))

    def test_names_and_placeholders_are_not_secrets(self):
        for text in ("set CLAUDE_CODE_OAUTH_TOKEN in a file readable only by the lab user",
                     "printf 'export CLAUDE_CODE_OAUTH_TOKEN=%s\\n' \"$T\"",
                     "GEMINI_API_KEY=$GEMINI_API_KEY", '"refresh_token": ""', "sk-ant- is the prefix of Anthropic tokens",
                     "the token starts with sk-ant-oat"):
            self.assertEqual(self.kinds(text), set(), text)

    def test_line_numbers_and_no_value_in_the_report(self):
        v = fake("google")
        found = sg.scan_bytes(("a\nb\n" + v + "\n").encode())
        self.assertEqual(found, [("google-api-key", 3)])
        out = io.StringIO()
        sg.report([{"path": "f", "member": None, "kind": "google-api-key", "line": 3}], [], out)
        self.assertNotIn(v, out.getvalue())


class Fingerprints(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.claude, self.codex, self.gem = fake("opaque"), fake("opaque"), fake("opaque")
        (self.home / ".agentlab_claude_token").write_text(self.claude + "\n")
        (self.home / ".codex").mkdir()
        (self.home / ".codex/auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"refresh_token": self.codex,
                                                                                                   "account_id": "acct-" + "1" * 20}}))
        (self.home / ".claude").mkdir()
        (self.home / ".claude/.credentials.json").write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": fake("opaque"), "scopes": ["user:inference-and-more"]},
             "mcpOAuth": {"srv": {"serverName": "an-account-connector-name", "serverUrl": "https://connector.example/mcp"}}}))
        (self.home / ".agentlab_secrets").write_text(f"export GEMINI_API_KEY='{self.gem}'\nSHORT=abc\n")

    def test_values_are_fingerprinted_never_kept(self):
        d = sg.fingerprints(self.home)
        self.assertEqual(d["unreadable"], [])
        self.assertEqual({f["kind"] for f in d["fingerprints"]}, {"claude-oauth-token", "claude-login", "codex-login", "lab-secret"})
        self.assertEqual(sum(f["kind"] == "claude-login" for f in d["fingerprints"]), 1,
                         "only the token field: connector names, URLs and scopes are account data, not secrets")
        self.assertEqual(sum(f["kind"] == "codex-login" for f in d["fingerprints"]), 1, "account_id is not a secret")
        blob = json.dumps(d)
        for v in (self.claude, self.codex, self.gem):
            self.assertNotIn(v, blob)
        self.assertNotIn("abc", [f["prefix"] for f in d["fingerprints"]], "values shorter than MIN_LEN are not fingerprinted")
        self.assertIn(".codex/auth.json", [f["source"] for f in d["fingerprints"]])

    def test_exact_match_finds_an_opaque_secret_in_a_nested_archive(self):
        fps = sg.fingerprints(self.home)["fingerprints"]
        inner = tgz({"logs/session.jsonl": ('{"msg": "ran with ' + self.codex + '"}\n').encode()})
        outer = tgz({"client-logs.tgz": inner})
        run = Path(tempfile.mkdtemp())
        (run / "vm-out.tgz").write_bytes(outer)
        (run / "clean.txt").write_text("nothing here")
        findings, failures = sg.scan_paths([run], fps)
        self.assertEqual(failures, [])
        self.assertEqual({f["kind"] for f in findings}, {"codex-login"})
        self.assertIn("client-logs.tgz/logs/session.jsonl", [f["member"] for f in findings])  # plus the whole tar streams
        self.assertEqual(sg.scan_paths([run], [])[0], [], "an opaque value is found only by its fingerprint")

    def test_unreadable_store_is_reported_and_refused(self):
        (self.home / ".config/cursor").mkdir(parents=True)
        (self.home / ".config/cursor/auth.json").write_text("{not json")
        d = sg.fingerprints(self.home)
        self.assertEqual([u["source"] for u in d["unreadable"]], [".config/cursor/auth.json"])
        f = self.home / "fp.json"
        f.write_text(json.dumps(d))
        with self.assertRaises(SystemExit):
            sg.load_fps(str(f))

    def test_merge_keeps_a_renewed_secret_and_the_old_one(self):
        a = {"fingerprints": [sg.fingerprint(fake("opaque"), "codex-login", "x")]}
        b = {"fingerprints": [sg.fingerprint(fake("opaque"), "codex-login", "x")], "created": 2}
        m = sg.merge_fingerprints(a, b)
        self.assertEqual(len(m["fingerprints"]), 2)
        self.assertEqual(len(sg.merge_fingerprints(m, b)["fingerprints"]), 2)


class FailsClosed(unittest.TestCase):
    def test_corrupt_archive_and_unsupported_format(self):
        d = Path(tempfile.mkdtemp())
        (d / "broken.tgz").write_bytes(b"\x1f\x8b" + os.urandom(64))
        (d / "x.7z").write_bytes(b"7z")
        findings, failures = sg.scan_paths([d])
        self.assertEqual(findings, [])
        self.assertEqual(sorted(Path(f["path"]).name for f in failures), ["broken.tgz", "x.7z"])
        self.assertEqual(sg.report(findings, failures, io.StringIO()), 2)

    def test_missing_path(self):
        self.assertEqual(sg.scan_paths(["/nonexistent/q4"])[1][0]["error"], "does not exist")

    def test_zip_gzip_and_skip(self):
        d = Path(tempfile.mkdtemp())
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("a/b.txt", "key " + fake("google"))
        (d / "e.zip").write_bytes(z.getvalue())
        (d / "j.log.gz").write_bytes(gzip.compress(fake("jwt").encode()))
        (d / "attempts").mkdir()
        (d / "attempts/old.txt").write_text(fake("anthropic"))
        kinds = {f["kind"] for f in sg.scan_paths([d], skip={"attempts"})[0]}  # a stored zip is also found in its raw bytes
        self.assertEqual(kinds, {"google-api-key", "jwt"})


class Git(unittest.TestCase):
    def git(self, *a):
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True,
                       capture_output=True)

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.git("init", "-q")
        (self.repo / "a.txt").write_text("hello")
        self.git("add", ".")
        self.git("commit", "-qm", "first")

    def head(self):
        return subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def test_secret_added_then_removed_is_still_found(self):
        base = self.head()
        (self.repo / "doc.md").write_text("token " + fake("anthropic"))
        self.git("add", ".")
        self.git("commit", "-qm", "add")
        (self.repo / "doc.md").write_text("token removed")
        self.git("add", ".")
        self.git("commit", "-qm", "remove")
        findings, failures = sg.scan_git(self.repo, base, self.head())
        self.assertEqual(failures, [])
        self.assertEqual([f["kind"] for f in findings], ["anthropic-token"])

    def test_commit_message_and_whole_history(self):
        (self.repo / "b.txt").write_text("fine")
        self.git("add", ".")
        self.git("commit", "-qm", "note: " + fake("google"))
        findings, _ = sg.scan_git(self.repo, sg.EMPTY_TREE, self.head())
        self.assertEqual([(f["kind"], "message" in f["path"]) for f in findings], [("google-api-key", True)])

    def test_clean_range_and_bad_revision(self):
        self.assertEqual(sg.scan_git(self.repo, sg.EMPTY_TREE, self.head()), ([], []))
        self.assertEqual(len(sg.scan_git(self.repo, "nonexistent", self.head())[1]), 1)

    def test_cli_exit_codes(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "secret_gate.py"), "scan-git", "--formats-only", "--repo",
                            str(self.repo), sg.EMPTY_TREE, "HEAD"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, str(SCRIPTS / "secret_gate.py"), "scan-git", "--repo", str(self.repo), sg.EMPTY_TREE,
                            "HEAD"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, "neither fingerprints nor an explicit --formats-only: refused")

    def test_secret_only_in_a_merge_resolution(self):  # D26-R1
        base = self.head()
        self.git("checkout", "-qb", "side")
        (self.repo / "s.txt").write_text("side")
        self.git("add", "."); self.git("commit", "-qm", "side")
        self.git("checkout", "-q", "-")
        (self.repo / "m.txt").write_text("main")
        self.git("add", "."); self.git("commit", "-qm", "main")
        self.git("merge", "-q", "--no-commit", "side")
        (self.repo / "resolved.txt").write_text("key " + fake("google"))  # in neither parent
        self.git("add", "."); self.git("commit", "-qm", "merge")
        kinds = [f["kind"] for f in sg.scan_git(self.repo, base, self.head())[0]]
        self.assertEqual(kinds, ["google-api-key"])

    def test_secret_in_a_file_name_is_found_and_redacted(self):  # D26-R3
        v = fake("anthropic")
        (self.repo / ("note-" + v + ".txt")).write_text("")
        self.git("add", "."); self.git("commit", "-qm", "empty file")
        findings, failures = sg.scan_git(self.repo, sg.EMPTY_TREE, self.head())
        self.assertIn("anthropic-token (in a file name)", [f["kind"] for f in findings])
        self.assertNotIn(v, json.dumps([findings, failures]))


class ReviewRound1(unittest.TestCase):
    """Codex round 1 on D26: names redacted, traversal errors, renamed archives, fingerprint files validated."""

    def test_member_name_with_a_secret_is_found_and_redacted(self):
        v = fake("anthropic")
        d = Path(tempfile.mkdtemp())
        (d / "x.tgz").write_bytes(tgz({"dir/" + v + ".log": b"harmless"}))
        out = io.StringIO()
        findings, failures = sg.scan_paths([d], full_fps())
        sg.report(findings, failures, out)
        self.assertIn("(in a member name)", out.getvalue())
        self.assertNotIn(v, out.getvalue())
        self.assertIn(sg.REDACTED, out.getvalue())

    def test_exact_value_in_a_file_name_is_redacted(self):
        v = fake("opaque")
        d = Path(tempfile.mkdtemp())
        (d / ("run-" + v)).write_text("x")
        fps = full_fps(sg.fingerprint(v, "codex-login", "x"))
        findings, _ = sg.scan_paths([d], fps)
        self.assertEqual([f["kind"] for f in findings], ["codex-login (in a file name)"])
        self.assertNotIn(v, json.dumps(findings))

    @unittest.skipIf(os.geteuid() == 0, "root can read anything")
    def test_unreadable_directory_is_a_failure(self):  # D26-R4
        d = Path(tempfile.mkdtemp())
        (d / "locked").mkdir()
        (d / "locked/f.txt").write_text(fake("google"))
        os.chmod(d / "locked", 0)
        try:
            findings, failures = sg.scan_paths([d])
        finally:
            os.chmod(d / "locked", 0o755)
        self.assertEqual(findings, [])
        self.assertTrue(any("cannot list" in f["error"] for f in failures))

    def test_symlinked_directory_is_a_failure(self):
        d, other = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (other / "f.txt").write_text("x")
        (d / "link").symlink_to(other, target_is_directory=True)
        self.assertEqual([f["error"] for f in sg.scan_paths([d])[1]], ["symlinked directory not followed"])

    def test_renamed_unsupported_archives_fail_closed(self):  # D26-R5
        d = Path(tempfile.mkdtemp())
        (d / "capture.zstd").write_bytes(b"\x28\xb5\x2f\xfd" + os.urandom(32))
        (d / "notes.txt").write_bytes(b"7z\xbc\xaf\x27\x1c" + os.urandom(32))
        (d / "data.bin").write_bytes(b"Rar!\x1a\x07\x00" + os.urandom(32))
        failures = sg.scan_paths([d])[1]
        self.assertEqual(sorted(Path(f["path"]).name for f in failures), ["capture.zstd", "data.bin", "notes.txt"])

    def test_fingerprint_file_is_validated(self):  # D26-R7
        d = Path(tempfile.mkdtemp())
        cases = {"missing": None, "no-list": {"x": 1}, "bad-entry": {"fingerprints": [{"kind": "codex-login"}]},
                 "incomplete": {"fingerprints": [sg.fingerprint(fake("opaque"), "codex-login", "x")]},
                 "unreadable": {"fingerprints": full_fps(), "unreadable": [{"source": ".codex/auth.json"}]}}
        for name, content in cases.items():
            f = d / f"{name}.json"
            if content is not None:
                f.write_text(json.dumps(content))
            with self.assertRaises(SystemExit) as cm:
                sg.load_fps(str(f))
            self.assertEqual(cm.exception.code, 2, name)
        ok = d / "ok.json"
        ok.write_text(json.dumps({"fingerprints": full_fps(), "unreadable": []}))
        self.assertEqual(len(sg.load_fps(str(ok))), len(sg.REQUIRED_KINDS))

    def test_cli_scan_needs_a_valid_fingerprint_file(self):
        d = Path(tempfile.mkdtemp())
        (d / "a.txt").write_text("fine")
        r = subprocess.run([sys.executable, str(SCRIPTS / "secret_gate.py"), "scan", "--fingerprints", str(d / "none.json"),
                            str(d)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class ReviewRound2(unittest.TestCase):
    """Codex round 2 on D26: fingerprint entries that can never match, per-name readings of one blob, tags and ref names."""

    def test_entries_that_cannot_match_are_refused(self):  # D26-R7
        good = full_fps()
        for bad in ({"sha256": "z" * 64}, {"length": 3}, {"length": 20.0}, {"prefix": "ab"}, {"kind": ""}, {"source": None}):
            entry = dict(good[0], **bad)
            self.assertTrue(sg.fingerprint_problems({"fingerprints": [entry] + good[1:], "unreadable": []}), bad)
        self.assertEqual(sg.fingerprint_problems({"fingerprints": good, "unreadable": []}), [])
        self.assertTrue(sg.fingerprint_problems({"fingerprints": good, "unreadable": "x"}))

    def git(self, repo, *a):
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True, capture_output=True)

    def repo(self):
        r = Path(tempfile.mkdtemp())
        self.git(r, "init", "-q")
        return r

    def test_one_blob_two_names_two_readings(self):  # D26-R8
        r = self.repo()
        junk = b"PK\x03\x04" + os.urandom(40)  # looks like a zip, is not one
        (r / "a.txt").write_bytes(junk)
        self.git(r, "add", "."); self.git(r, "commit", "-qm", "one")
        (r / "broken.zip").write_bytes(junk)
        self.git(r, "add", "."); self.git(r, "commit", "-qm", "two")
        _, failures = sg.scan_git(r, sg.EMPTY_TREE, "HEAD")
        self.assertTrue(failures, "the corrupt archive is not accepted because the same bytes were seen first as a.txt")

    def test_zip_with_data_in_front(self):
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("inner/k.txt", ("key " + fake("google") + " ") * 50)
        d = Path(tempfile.mkdtemp())
        (d / "installer.bin").write_bytes(b"#!/bin/sh\necho self-extracting\n" + os.urandom(64) + z.getvalue())
        kinds = {f["kind"] for f in sg.scan_paths([d], full_fps())[0]}
        self.assertIn("google-api-key", kinds)

    def test_annotated_tag_and_ref_name(self):  # D26-R9
        r = self.repo()
        (r / "a.txt").write_text("clean")
        self.git(r, "add", "."); self.git(r, "commit", "-qm", "clean")
        self.git(r, "tag", "-a", "v1", "-m", "release note " + fake("google"))
        findings, failures = sg.scan_git(r, sg.EMPTY_TREE, "v1")
        self.assertEqual(failures, [])
        self.assertTrue(any(f["path"].startswith("tag object") for f in findings))
        v = fake("anthropic")
        findings, _ = sg.scan_git(r, sg.EMPTY_TREE, "HEAD", ref_names=["refs/heads/x-" + v])
        self.assertEqual([f["kind"] for f in findings], ["anthropic-token (in a ref name)"])
        self.assertNotIn(v, json.dumps(findings))


class ReviewRound3(unittest.TestCase):
    """Codex round 3 on D26: data hidden in front of a clean zip is not skipped."""

    def clean_zip(self):
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("readme.txt", "nothing here")
        return z.getvalue()

    def test_gzip_secret_in_front_of_a_clean_zip(self):
        blob = gzip.compress(("token " + fake("anthropic")).encode()) + self.clean_zip()
        for name in ("data.bin", "data.gz", "data.zip"):
            findings, failures = sg.scan_blob(name, blob, full_fps())
            self.assertTrue(findings or failures, name)  # found, or refused: never a clean pass

    def test_unsupported_archive_in_front_of_a_clean_zip(self):
        blob = b"\x28\xb5\x2f\xfd" + os.urandom(64) + self.clean_zip()
        findings, failures = sg.scan_blob("capture.bin", blob, full_fps())
        self.assertTrue(failures)

    def test_ordinary_archives_still_pass(self):
        inner = tgz({"a/b.txt": b"fine"})
        outer = tgz({"inner.tgz": inner, "x.txt": b"fine"})
        self.assertEqual(sg.scan_blob("run/client-logs.tgz", outer, full_fps()), ([], []))
        self.assertEqual(sg.scan_blob("e.zip", self.clean_zip(), full_fps()), ([], []))
        self.assertEqual(sg.scan_blob("l.log.gz", gzip.compress(b"plain"), full_fps()), ([], []))


class PrivateArchivesAndRedaction(unittest.TestCase):
    """Paolo's rule of 17 Oct 2026: token-shaped data in the private raw logs is counted, not blocking; uses go through redaction."""

    def test_socket_is_skipped(self):
        import socket as _socket
        d = Path(tempfile.mkdtemp())
        s = _socket.socket(_socket.AF_UNIX)
        s.bind(str(d / "worker.sock"))
        try:
            self.assertEqual(sg.scan_paths([d], full_fps()), ([], []))
        finally:
            s.close()

    def test_exact_and_format_are_told_apart(self):
        v = fake("opaque")
        f_exact = {"kind": "codex-login", "path": "p", "member": None, "line": None}
        f_fmt = {"kind": "jwt (in a member name)", "path": "p", "member": "m", "line": None}
        self.assertTrue(sg.is_exact(f_exact))
        self.assertFalse(sg.is_exact(f_fmt))
        self.assertEqual(sg.base_kind(f_fmt["kind"]), "jwt")

    def test_redacted_text_keeps_its_length_and_is_clean(self):
        v = fake("opaque")
        fps = full_fps(sg.fingerprint(v, "lab-secret", "x"))
        text = ('{"a": "' + fake("jwt") + '", "b": "' + v + '"}\n').encode()
        body, n = sg.redact_blob("x.jsonl", text, fps)
        self.assertEqual((len(body), n), (len(text), 2))
        self.assertEqual(sg.scan_blob("x.jsonl", body, fps), ([], []))
        self.assertNotIn(v.encode(), body)

    def test_redacted_sqlite_is_still_a_database(self):
        import sqlite3
        d = Path(tempfile.mkdtemp())
        db = d / "logs_2.sqlite"
        con = sqlite3.connect(db)
        con.execute("create table logs (id integer, feedback_log_body text)")
        con.executemany("insert into logs values (?, ?)", [(i, "auth " + fake("jwt") + " ok") for i in range(20)])
        con.commit(); con.close()
        body, n = sg.redact_blob(str(db), db.read_bytes(), full_fps())
        self.assertEqual(n, 20)
        out = d / "red.sqlite"
        out.write_bytes(body)
        rows = sqlite3.connect(out).execute("select count(*), min(length(feedback_log_body)) from logs").fetchone()
        self.assertEqual(rows[0], 20)
        self.assertEqual(sg.scan_blob(str(out), body, full_fps()), ([], []))

    def test_redacted_archives_are_rebuilt_clean(self):
        inner = tgz({"chat/" + fake("anthropic") + ".json": ("tok " + fake("jwt")).encode(), "ok.txt": b"fine"})
        outer = tgz({"home/.gemini/inner.tgz": inner, "manifest.json": b"{}"})
        body, n = sg.redact_blob("client-logs.tgz", outer, full_fps())
        self.assertGreaterEqual(n, 2)
        self.assertEqual(sg.scan_blob("client-logs.tgz", body, full_fps()), ([], []))
        names = [m for m, _ in sg._open_archive("client-logs.tgz", body)]
        self.assertIn("manifest.json", names)
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("a.txt", "k " + fake("google"))
        zbody, _ = sg.redact_blob("e.zip", z.getvalue(), full_fps())
        self.assertEqual(sg.scan_blob("e.zip", zbody, full_fps()), ([], []))
        gbody, _ = sg.redact_blob("l.log.gz", gzip.compress(fake("jwt").encode()), full_fps())
        self.assertEqual(sg.scan_blob("l.log.gz", gbody, full_fps()), ([], []))

    def test_ambiguous_archive_is_not_rebuilt(self):
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w") as zf:
            zf.writestr("r.txt", "clean")
        polyglot = gzip.compress(b"x") + zbuf.getvalue()
        with self.assertRaises(sg.Unscannable):
            sg.redact_blob("data.bin", polyglot, full_fps())

    def test_cli_redact(self):
        d = Path(tempfile.mkdtemp())
        (d / "run").mkdir()
        (d / "run" / "a.jsonl").write_text("x " + fake("jwt") + "\n")
        fp = d / "fp.json"
        fp.write_text(json.dumps({"fingerprints": full_fps(), "unreadable": []}))
        out = d / "red"
        r = subprocess.run([sys.executable, str(SCRIPTS / "secret_gate.py"), "redact", "--fingerprints", str(fp), "--out", str(out),
                            str(d / "run")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["written"][0]["spans"], 1)
        self.assertEqual(sg.scan_paths([out], full_fps()), ([], []))
        r = subprocess.run([sys.executable, str(SCRIPTS / "secret_gate.py"), "redact", "--fingerprints", str(fp), "--out", str(out),
                            str(d / "run")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, "an existing output folder is refused")


class ReviewRound4(unittest.TestCase):
    """Codex round 4 on D26: whole compressed tar streams, tar metadata, JSON-safe redaction, no colliding copies."""

    def tar_bytes(self, members, comp=""):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode=("w:" + comp) if comp else "w") as t:
            for name, data in members.items():
                info = tarfile.TarInfo(name); info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def test_secret_after_the_end_of_a_compressed_tar(self):  # R11
        blob = self.tar_bytes({"ok.txt": b"fine"}, "gz") + gzip.compress(("tok " + fake("anthropic")).encode())
        findings, failures = sg.scan_blob("evidence.tgz", blob, full_fps())
        self.assertTrue(failures, "trailing data after the tar end is refused")
        self.assertIn("anthropic-token", {f["kind"] for f in findings}, "and the whole stream is scanned")

    def test_secret_in_tar_metadata_is_found_and_redacted(self):  # R11
        v = fake("anthropic")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as t:
            info = tarfile.TarInfo("link"); info.type = tarfile.SYMTYPE; info.linkname = "/tmp/" + v
            info.uname = "user"
            t.addfile(info)
        blob = buf.getvalue()
        self.assertIn("anthropic-token", {f["kind"] for f in sg.scan_blob("m.tgz", blob, full_fps())[0]})
        body, n = sg.redact_blob("m.tgz", blob, full_fps())
        self.assertGreaterEqual(n, 1)
        self.assertEqual(sg.scan_blob("m.tgz", body, full_fps()), ([], []))

    def test_json_stays_parseable_and_keys_stay(self):  # R13
        rec = {"tokens": {"access_token": fake("opaque") + "Z" * 8, "id": 1}, "note": "x " + fake("jwt")}
        data = json.dumps(rec).encode()
        self.assertTrue(sg.scan_blob("auth.json", data, full_fps())[0])
        body, n = sg.redact_blob("auth.json", data, full_fps())
        out = json.loads(body)
        self.assertIn("access_token", out["tokens"])
        self.assertTrue(set(out["tokens"]["access_token"]) == {"*"})
        self.assertEqual(sg.scan_blob("auth.json", body, full_fps()), ([], []), "masking placeholders are not findings")
        lines = (json.dumps(rec) + "\n" + json.dumps({"a": 1}) + "\n").encode()
        lbody, _ = sg.redact_blob("s.jsonl", lines, full_fps())
        self.assertEqual(len([json.loads(l) for l in lbody.decode().splitlines()]), 2)

    def test_same_names_from_two_inputs_do_not_collide(self):  # R14
        d = Path(tempfile.mkdtemp())
        for run in ("run1", "run2"):
            (d / run).mkdir()
            (d / run / "client-logs.tgz").write_bytes(tgz({"a.txt": (run + " " + fake("jwt")).encode()}))
        written, failures = sg.redact_paths([d / "run1" / "client-logs.tgz", d / "run2" / "client-logs.tgz"], d / "out", full_fps())
        self.assertEqual(failures, [])
        self.assertEqual(sorted(w["path"] for w in written), ["01/client-logs.tgz", "02/client-logs.tgz"])

    def test_redaction_that_makes_names_equal_is_refused(self):  # R14
        a, b = fake("anthropic"), fake("anthropic")
        blob = tgz({"x-" + a + ".log": b"1", "x-" + b + ".log": b"2"})
        with self.assertRaises(sg.Unscannable):
            sg.redact_blob("c.tgz", blob, full_fps())


class Orchestrator(unittest.TestCase):
    """q4_events.secret_problems: fingerprints come from the VM over ssh; the host state keeps them; findings stop the run."""

    def host(self, fps, rc=0):
        class H:
            def ssh(self, ip, cmd, timeout=120):
                assert "secret_gate.py fingerprints" in cmd
                return _NS(returncode=rc, stdout=json.dumps(fps), stderr="")
        return H()

    def test_clean_run_and_state_file(self):
        d = Path(tempfile.mkdtemp())
        (d / "cell.stdout.txt").write_text("ok")
        v = fake("opaque")
        self.assertEqual(q4.secret_problems(self.host({"fingerprints": full_fps(sg.fingerprint(v, "lab-secret", "s")),
                                                       "unreadable": []}), "ip", d), [])
        st = q4.STATE / "secret-fingerprints.json"
        self.assertEqual(oct(st.stat().st_mode & 0o777), "0o600")
        self.assertNotIn(v, st.read_text())

    def test_finding_names_file_and_kind_only(self):
        d = Path(tempfile.mkdtemp())
        v = fake("opaque")
        (d / "client-logs.tgz").write_bytes(tgz({"home/.gemini/tmp/chat.json": ("using " + v).encode()}))
        probs = q4.secret_problems(self.host({"fingerprints": full_fps(sg.fingerprint(v, "lab-secret", "s")), "unreadable": []}),
                                   "ip", d)
        self.assertEqual(len(probs), 1)
        self.assertIn("client-logs.tgz", probs[0])
        self.assertIn("lab-secret", probs[0])
        self.assertNotIn(v, probs[0])

    def test_token_shaped_data_in_the_private_archive_is_marked_not_blocking(self):
        d = Path(tempfile.mkdtemp())
        jwt = fake("jwt")
        (d / "client-logs.tgz").write_bytes(tgz({"home/.codex/logs_2.sqlite": ("body " + jwt).encode()}))
        (d / "cell.stdout.txt").write_text("ok")
        probs = q4.secret_problems(self.host({"fingerprints": full_fps(), "unreadable": []}), "ip", d)
        self.assertEqual(probs, [])
        marks = json.loads((d / "secret-marks.json").read_text())
        self.assertEqual(marks["token_shaped_by_kind"], {"jwt": 1})
        self.assertNotIn(jwt, json.dumps(marks))
        red = (d / "client-logs.redacted.tgz").read_bytes()  # R12: the copy every later reader uses
        self.assertEqual(sg.scan_blob("client-logs.redacted.tgz", red, full_fps()), ([], []))
        self.assertEqual(marks["redacted_copy"]["redacted_sha256"], __import__("hashlib").sha256(red).hexdigest())

    def test_lab_secret_in_the_private_archive_still_stops(self):
        d = Path(tempfile.mkdtemp())
        v = fake("opaque")
        (d / "client-logs.tgz").write_bytes(tgz({"home/.claude/projects/x.jsonl": ("env " + v).encode()}))
        probs = q4.secret_problems(self.host({"fingerprints": full_fps(sg.fingerprint(v, "claude-oauth-token", "x")),
                                              "unreadable": []}), "ip", d)
        self.assertTrue(probs and "claude-oauth-token" in probs[0])

    def test_token_shaped_data_outside_the_private_archive_stops(self):
        d = Path(tempfile.mkdtemp())
        (d / "client-logs.tgz").write_bytes(tgz({"a.txt": b"fine"}))
        (d / "telemetry").mkdir()
        (d / "telemetry" / "journal.txt").write_text("header " + fake("jwt"))
        probs = q4.secret_problems(self.host({"fingerprints": full_fps(), "unreadable": []}), "ip", d)
        self.assertTrue(probs and "journal.txt" in probs[0])

    def test_unusable_fingerprints_from_the_vm_or_state(self):  # D26-R7
        d = Path(tempfile.mkdtemp())
        bad = [dict(f, sha256="z" * 64) for f in full_fps()]
        self.assertIn("not usable", q4.secret_problems(self.host({"fingerprints": bad, "unreadable": []}), "ip", d)[0])
        st = q4.STATE / "secret-fingerprints.json"
        st.write_text('{"fingerprints": "nope"}')
        try:
            self.assertIn("malformed", q4.secret_problems(self.host({"fingerprints": full_fps(), "unreadable": []}), "ip", d)[0])
        finally:
            st.unlink()

    def test_collection_records_are_redacted_and_rescanned(self):  # R12
        jwt = fake("jwt")
        class CC:
            def check(self, *a): return {"export": "ok", "client_logs": "wrote", "pattern_of": {"home/.x/" + jwt + ".log": ["p"]}}
            def public_record(self, rec): return {k: rec[k] for k in ("export", "client_logs")}
        d = Path(tempfile.mkdtemp())
        rec = q4.Runner(None, None).check_client_logs(d, "codex", 1, 2, CC(), None, None, 0)
        self.assertNotIn(jwt, (d / "collection.json").read_text())
        self.assertFalse(rec.get("secret_problems"))

    def test_client_logs_are_checked_only_after_the_gate(self):  # D26-R2
        class CC:
            def check(self, *a): raise AttributeError("malformed manifest")
            def public_record(self, rec): return {k: rec[k] for k in ("export", "reason") if k in rec}
        rn = q4.Runner(None, None)
        d = Path(tempfile.mkdtemp())
        rec = rn.check_client_logs(d, "codex", 1, 2, CC(), None, None, 0)
        self.assertEqual(rec["export"], "failed")
        self.assertIn("collection check failed", rec["reason"])
        self.assertTrue((d / "collection-public.json").exists())

    def test_fails_closed_without_fingerprints(self):
        d = Path(tempfile.mkdtemp())
        self.assertIn("no fingerprint of", q4.secret_problems(self.host({"fingerprints": [], "unreadable": []}), "ip", d)[0],
                      "a store that vanished from the VM leaves its secret unchecked")
        self.assertTrue(q4.secret_problems(self.host({"fingerprints": [], "unreadable": [{"source": ".codex/auth.json"}]}, rc=2),
                                           "ip", d))

        class Broken:
            def ssh(self, ip, cmd, timeout=120):
                return _NS(returncode=255, stdout="", stderr="no route")
        self.assertTrue(q4.secret_problems(Broken(), "ip", d))


if __name__ == "__main__":
    unittest.main()
