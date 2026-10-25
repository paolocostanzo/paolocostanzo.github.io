#!/usr/bin/env python3
"""Secret gate (D26). The author's rule of 17 Oct 2026: no secret may ever be published. A secret here is any token, API key or
login file of the lab's clients, and anything inside them. This script never prints, logs or writes a secret's value.

  python3 secret_gate.py fingerprints                          # INSIDE the VM: JSON fingerprints of every lab secret on stdout
  python3 secret_gate.py scan --fingerprints FP.json PATH...   # files, folders and archives; exit 1 on a finding
  python3 secret_gate.py scan-git --fingerprints FP.json [--repo DIR] [--ref-name REF]... BASE HEAD
                                                               # every blob and path name in the tree of every commit in
                                                               # BASE..HEAD (merges included), the commit messages, annotated
                                                               # tag objects reached from HEAD, and the ref names;
                                                               # BASE may be the empty tree (whole history)
  --formats-only replaces --fingerprints for an explicit audit without exact matching. A fingerprint file that is missing,
  malformed, lists unreadable stores or lacks a required kind (--require-kinds) stops the scan (exit 2).

Two kinds of matching:
- *exact*: a fingerprint is {kind, source, length, prefix (first 4 characters), sha256}. Every occurrence of a prefix is checked by
  hashing the bytes of the recorded length that start there. Exact matching needs fingerprints taken inside the VM and finds a
  secret whatever its format;
- *format*: known token formats (Anthropic, Google API keys, JWTs, OpenAI keys, GitHub tokens, private keys, token fields in JSON,
  secret environment variables with a literal value). It needs no fingerprint, and also finds a secret that was renewed after the
  fingerprints were taken.

Archives (.tar, .tgz, .tar.gz, .gz, .zip, .xz, .bz2) are scanned member by member, nested up to three levels. A file that cannot
be read or decompressed, a directory that cannot be listed, a symlinked directory, or a compressed format recognised by its
signature but not supported (zstandard, 7-zip, rar, lz4, lzip, compress, lzh, cabinet), whatever the file is called, is a failure
(exit 2): the gate fails closed. Formats without a signature (raw deflate, brotli) cannot be recognised and are scanned as bytes.
File names and archive member names are scanned too; a git blob stored under several names is scanned once per way those names
would make it be read, and a zip is recognised by its end record too (self-extracting or with data in front). Findings are reported with the file, the archive member, the line (format
matches) and the kind, never with the value; every reported name or error is redacted first.
Exit codes: 0 clean, 1 at least one finding, 2 something could not be scanned (and no finding).
"""
import argparse, bz2, gzip, hashlib, io, json, lzma, os, re, socket, stat, subprocess, sys, tarfile, time, zipfile
from pathlib import Path

MIN_LEN = 16          # shorter values are not fingerprinted (too many accidental matches, and not secrets)
PREFIX = 4
MAX_DEPTH = 3
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

FORMATS = [
    ("anthropic-token", rb"sk-ant-[a-z]{2,8}[0-9]{0,3}-[A-Za-z0-9_\-]{20,}"),
    ("google-api-key", rb"AIza[0-9A-Za-z_\-]{35}"),
    ("jwt", rb"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    ("openai-key", rb"(?<![A-Za-z0-9_\-])sk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{32,}"),
    ("github-token", rb"(?<![A-Za-z0-9_])(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,}"),
    ("private-key", rb"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"),
    ("token-field", rb"[\"'](?:access_token|refresh_token|id_token|accessToken|refreshToken|idToken|api_key|apiKey|"
                    rb"client_secret|clientSecret)[\"']\s*:\s*[\"'](?P<v>(?!\*+[\"'])[^\"'\s]{16,})[\"']"),
    ("secret-env", rb"(?<![A-Za-z0-9_])(?:CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|GEMINI_API_KEY|"
                   rb"GOOGLE_API_KEY|OPENAI_API_KEY|CODEX_ACCESS_TOKEN|DASHSCOPE_API_KEY|OPENROUTER_API_KEY)\s*[=:]\s*"
                   rb"[\"']?(?![\"']?\$)(?P<v>[A-Za-z0-9_\-./+=]{16,})"),
]
FORMATS = [(k, re.compile(p)) for k, p in FORMATS]
FORMAT_KINDS = {k for k, _ in FORMATS}


def base_kind(kind):
    """'jwt (in a member name)' -> 'jwt'."""
    return kind.split(" (", 1)[0]


def is_exact(finding):
    """True for a fingerprinted lab secret, False for a match by format only."""
    return base_kind(finding["kind"]) not in FORMAT_KINDS

# --------------------------------------------------------------------------- fingerprints (inside the VM)
HOME = Path(os.path.expanduser("~"))
# (kind, path relative to HOME, how to read it)
SOURCES = [
    ("claude-oauth-token", ".agentlab_claude_token", "raw"),
    ("claude-login", ".claude/.credentials.json", "json-keys"),
    ("claude-login", ".agentlab_claude_interactive.json", "json-keys"),
    ("codex-login", ".codex/auth.json", "json-keys"),
    ("cursor-login", ".config/cursor/auth.json", "json-keys"),
    ("lab-secret", ".agentlab_secrets", "env"),
    ("goose-secret", ".config/goose/secrets.yaml", "yaml"),
    ("gemini-settings", ".gemini/settings.json", "json-keys"),
    ("qwen-settings", ".qwen/settings.json", "json-keys"),
    ("gemini-env", ".gemini/.env", "env"),
    ("qwen-env", ".qwen/.env", "env"),
]
# Only values under these keys are secrets. Login files also hold account data that is not secret but appears in ordinary logs
# (Claude Code's file lists the account's MCP connectors by name and URL): fingerprinting it would stop every run for nothing.
SECRET_KEY = re.compile(r"(token|secret|password|passwd|credential|api[_-]?key|private[_-]?key)", re.I)


def _strings(obj, keyed=False, key=""):
    """String values of a JSON document: all of them, or (keyed) only those under a key that names a secret."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, keyed, str(k))
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, keyed, key)
    elif isinstance(obj, str) and (not keyed or SECRET_KEY.search(key)):
        yield obj


def values_of(path, how):
    text = path.read_text(encoding="utf-8", errors="replace")
    if how == "raw":
        return [text.strip()]
    if how in ("json", "json-keys"):
        return list(_strings(json.loads(text), keyed=(how == "json-keys")))
    if how == "env":
        out = []
        for line in text.splitlines():
            m = re.match(r"\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(.*)$", line)
            if m:
                out.append(m.group(1).strip().strip("'\""))
        return out
    if how == "yaml":  # flat key: value files only
        return [m.group(1).strip().strip("'\"") for m in (re.match(r"\s*[^:#]+:\s*(.+)$", l) for l in text.splitlines()) if m]
    raise ValueError(how)


def fingerprint(value, kind, source):
    b = value.encode()
    return {"kind": kind, "source": source, "length": len(b), "prefix": value[:PREFIX],
            "sha256": hashlib.sha256(b).hexdigest()}


def fingerprints(home=HOME, sources=SOURCES):
    """Fingerprints of every secret value found in the lab's known stores. A store that exists but cannot be read or parsed is
    listed under "unreadable": the caller must treat that as a failure, since its secrets would go unchecked."""
    fps, seen, unreadable, absent = [], set(), [], []
    for kind, rel, how in sources:
        p = home / rel
        if not p.exists():
            absent.append(rel)
            continue
        try:
            vals = values_of(p, how)
        except (OSError, ValueError) as exc:
            unreadable.append({"source": rel, "error": type(exc).__name__})
            continue
        for v in vals:
            if len(v.encode()) >= MIN_LEN and v not in seen:
                seen.add(v)
                fps.append(fingerprint(v, kind, rel))
    return {"created": int(time.time()), "host": socket.gethostname(), "fingerprints": fps, "unreadable": unreadable,
            "absent": absent}


def merge_fingerprints(old, new):
    """Union of two fingerprint files (a secret renewed during the campaign keeps the old fingerprint as well)."""
    out, seen = dict(new), set()
    out["fingerprints"] = []
    for f in (old or {}).get("fingerprints", []) + new.get("fingerprints", []):
        if f["sha256"] not in seen:
            seen.add(f["sha256"])
            out["fingerprints"].append(f)
    return out


# --------------------------------------------------------------------------- scanning
REQUIRED_KINDS = ("claude-oauth-token", "claude-login", "codex-login", "cursor-login", "lab-secret")  # the VM's stores (D26)
REDACTED = "[REDACTED]"
# compressed or archive formats recognised by their signature, whatever the file is called, that this script cannot open
UNSUPPORTED_MAGIC = [(b"\x28\xb5\x2f\xfd", "zstandard"), (b"7z\xbc\xaf\x27\x1c", "7-zip"), (b"Rar!\x1a\x07", "rar"),
                     (b"\x04\x22\x4d\x18", "lz4"), (b"LZIP", "lzip"), (b"\x1f\x9d", "unix compress"), (b"\x1f\xa0", "lzh"),
                     (b"MSCF", "cabinet")]


class Unscannable(Exception):
    pass


def _exact_spans(data, fps):
    """[(start, end, kind)] of every occurrence of a fingerprinted value."""
    spans = []
    for f in fps:
        pre, n, want = f["prefix"].encode(), f["length"], f["sha256"]
        i = data.find(pre)
        while i != -1:
            if hashlib.sha256(data[i:i + n]).hexdigest() == want:
                spans.append((i, i + n, f["kind"]))
            i = data.find(pre, i + 1)
    return spans


def _format_spans(data):
    """Spans of format matches: only the value where a pattern names it (group v), so that redaction keeps keys and syntax."""
    out = []
    for kind, rx in FORMATS:
        for m in rx.finditer(data):
            a, z = m.span("v") if "v" in rx.groupindex else m.span()
            out.append((a, z, kind))
    return out


def scan_bytes(data, fps=()):
    """[(kind, line)] for one blob of bytes, never the matched text; line is None for exact matches."""
    found, seen = [], set()
    for a, _, kind in _exact_spans(data, fps):
        if kind not in seen:  # one finding per kind and blob is enough to block, and says less
            seen.add(kind)
            found.append((kind, None))
    for a, _, kind in _format_spans(data):
        found.append((kind, data.count(b"\n", 0, a) + 1))
    return found


def redact(text, fps=()):
    """text with every secret-looking or fingerprinted part replaced: names and error messages are reported only this way."""
    if not text:
        return text
    b = text.encode(errors="surrogateescape")
    spans = sorted(_exact_spans(b, fps) + _format_spans(b))
    out, pos = [], 0
    for a, z, _ in spans:
        if a < pos:
            a = pos
        if a >= z:
            continue
        out.append(b[pos:a]); out.append(REDACTED.encode()); pos = z
    out.append(b[pos:])
    return b"".join(out).decode(errors="replace")


def _unsupported(data):
    for magic, name in UNSUPPORTED_MAGIC:
        if data.startswith(magic):
            return name
    return None


TAR_STREAM = "<whole tar stream>"


def _tar_stream(data):
    """The complete decompressed tar stream (every gzip, xz or bzip2 member, not only the first)."""
    comp = _compression(data)
    return {"gz": gzip.decompress, "xz": lzma.decompress, "bz2": bz2.decompress}[comp](data) if comp else data


def _tar_members(raw):
    """Parsed members of an uncompressed tar stream; anything but zero padding after the end of the archive is refused."""
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as t:
        members = t.getmembers()
        out = [(m, t.extractfile(m).read() if m.isfile() else b"") for m in members]
        end = t.offset
    if raw[end:].strip(b"\0"):
        raise Unscannable("data after the end of the tar archive")
    return out


def _read_tar(name, data):
    raw = _tar_stream(data)
    members = _tar_members(raw)
    # names of directories and links are published too, so they are kept (with no content); the whole stream is also scanned
    # flat, so that headers (link targets, owner names, pax records) are checked as well
    return [(m.name, body) for m, body in members] + [(TAR_STREAM, raw)]


def _read_zip(name, data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return [(i.filename, b"" if i.is_dir() else z.read(i)) for i in z.infolist()]


def _single(suffix, label, fn):
    def read(name, data):
        return [(name[:-len(suffix)] if name.lower().endswith(suffix) else name + label, fn(data))]
    return read


READERS = {"tar": _read_tar, "zip": _read_zip, "gz": _single(".gz", "!gunzip", gzip.decompress),
           "xz": _single(".xz", "!unxz", lzma.decompress), "bz2": _single(".bz2", "!bunzip2", bz2.decompress)}


def _open_archive(name, data):
    """Members of every archive reading that applies to these bytes, or None if none applies. Readings come from the name's
    suffix, from leading signatures and from a zip end record (a zip with data in front, or self-extracting). Every applicable
    reading is opened, so data hidden in front of a clean archive is not skipped; if any of them cannot be opened, or a known but
    unsupported format applies, the blob is Unscannable."""
    kinds = set()
    key = archive_key(name)
    if key:
        kinds.add(key)
    if len(data) > 262 and data[257:262] == b"ustar":
        kinds.add("tar")
    for magic, k in ((b"PK\x03\x04", "zip"), (b"\x1f\x8b", "gz"), (b"\xfd7zXZ\x00", "xz"), (b"BZh", "bz2")):
        if data.startswith(magic):
            kinds.add(k)
    if _unsupported(data):
        kinds.add("unsupported")
    try:
        if zipfile.is_zipfile(io.BytesIO(data)):
            kinds.add("zip")
    except (OSError, ValueError):
        pass
    if not kinds:
        return None
    if "unsupported" in kinds:
        raise Unscannable(f"archive format not supported: {_unsupported(data) or 'by suffix'}")
    if "tar" in kinds:  # the tar reader opens compressed tars itself; a gzip that is not a tar then fails there
        kinds -= {"gz", "xz", "bz2"}
    members = []
    for k in sorted(kinds):
        try:
            members += READERS[k](name, data)
        except (tarfile.TarError, zipfile.BadZipFile, OSError, EOFError, lzma.LZMAError, ValueError, KeyError) as exc:
            raise Unscannable(f"{k}: {type(exc).__name__}") from exc
    return members


def archive_key(name):
    """How a name alone steers archive handling (by suffix); bytes are also recognised by signature whatever the name."""
    low = name.lower()
    for key, sfx in (("tar", (".tar", ".tgz", ".tar.gz", ".tar.xz", ".tar.bz2", ".txz", ".tbz2")), ("zip", (".zip",)),
                     ("gz", (".gz",)), ("xz", (".xz",)), ("bz2", (".bz2",)),
                     ("unsupported", (".7z", ".zst", ".zstd", ".rar", ".lz4", ".lz", ".z", ".cab"))):
        if low.endswith(sfx):
            return key
    return ""


def scan_blob(path, data, fps=(), member=None, depth=0):
    """Findings [{path, member, kind, line}] and failures [{path, member, error}] for one blob: its content, the names of its
    archive members, and archives opened recursively. Every reported name is redacted."""
    findings, failures = [], []
    rp, rm = redact(path, fps), redact(member, fps)
    for kind, line in scan_bytes(data, fps):
        findings.append({"path": rp, "member": rm, "kind": kind, "line": line})
    try:
        members = _open_archive(member or path, data)
    except Unscannable as exc:
        if _compression(data):  # refused anyway; report what the whole decompressed stream holds, as far as it decompresses
            try:
                for kind, line in scan_bytes(_tar_stream(data), fps):
                    findings.append({"path": rp, "member": redact((member + "/" if member else "") + TAR_STREAM, fps),
                                     "kind": kind, "line": line})
            except (OSError, EOFError, lzma.LZMAError, ValueError):
                pass
        return findings, failures + [{"path": rp, "member": rm, "error": redact(str(exc), fps)}]
    if members is not None:
        if depth >= MAX_DEPTH:
            return findings, failures + [{"path": rp, "member": rm, "error": "archives nested too deep"}]
        for mname, mdata in members:
            full = (member + "/" if member else "") + mname
            for kind, _ in ([] if mname == TAR_STREAM else scan_bytes(mname.encode(errors="surrogateescape"), fps)):
                findings.append({"path": rp, "member": redact(full, fps), "kind": kind + " (in a member name)", "line": None})
            if mname == TAR_STREAM:  # scanned as bytes only: opening it again as an archive would never end
                for kind, line in scan_bytes(mdata, fps):
                    findings.append({"path": rp, "member": redact(full, fps), "kind": kind, "line": line})
                continue
            f2, e2 = scan_blob(path, mdata, fps, full, depth + 1)
            findings += f2
            failures += e2
    return findings, failures


def _walk_files(root, skip, failures, fps):
    """Every file under root, with traversal errors recorded (an unreadable directory is a failure, never a silent skip)."""
    out = []

    def onerror(exc):
        failures.append({"path": redact(str(getattr(exc, "filename", root)), fps), "member": None,
                         "error": f"cannot list: {type(exc).__name__}"})
    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        rel = Path(dirpath).relative_to(root).parts
        if set(rel) & set(skip):
            dirnames[:] = []
            continue
        for dn in list(dirnames):
            full = Path(dirpath) / dn
            if full.is_symlink():  # os.walk does not follow it: what it points to would go unchecked
                failures.append({"path": redact(str(full), fps), "member": None, "error": "symlinked directory not followed"})
                dirnames.remove(dn)
        out += [Path(dirpath) / f for f in filenames]
    return sorted(out)


def scan_paths(paths, fps=(), skip=()):
    findings, failures = [], []
    for root in paths:
        root = Path(root)
        if not root.exists():
            failures.append({"path": redact(str(root), fps), "member": None, "error": "does not exist"})
            continue
        files = [root] if not root.is_dir() else _walk_files(root, skip, failures, fps)
        for p in files:
            try:
                mode = p.stat().st_mode  # through a file symlink; a dangling one is a failure below
            except OSError as exc:
                failures.append({"path": redact(str(p), fps), "member": None, "error": type(exc).__name__})
                continue
            if not stat.S_ISREG(mode) and not stat.S_ISDIR(mode):
                continue  # socket, fifo or device: no content that a copy or a commit could carry
            for kind, _ in scan_bytes(str(p).encode(errors="surrogateescape"), fps):
                findings.append({"path": redact(str(p), fps), "member": None, "kind": kind + " (in a file name)", "line": None})
            try:
                data = p.read_bytes()  # a file symlink is read through: what it points to is what would be published
            except OSError as exc:
                failures.append({"path": redact(str(p), fps), "member": None, "error": type(exc).__name__})
                continue
            f, e = scan_blob(str(p), data, fps)
            findings += f
            failures += e
    return findings, failures


def _git(repo, *args, binary=False, stdin=None):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, input=stdin)
    if r.returncode != 0:
        raise Unscannable(f"git {args[0]} failed: {r.stderr.decode(errors='replace').strip()[:200]}")
    return r.stdout if binary else r.stdout.decode()


def _blobs(repo, shas):
    """Contents of many blobs through one `git cat-file --batch`."""
    if not shas:
        return {}
    raw = _git(repo, "cat-file", "--batch", binary=True, stdin=("\n".join(shas) + "\n").encode())
    out, pos = {}, 0
    for sha in shas:
        nl = raw.index(b"\n", pos)
        head = raw[pos:nl].split()
        if len(head) != 3 or head[1] != b"blob":
            raise Unscannable(f"cat-file: unexpected object for {sha[:12]}")
        size = int(head[2])
        out[sha] = raw[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1
    return out


def _peel_tags(repo, obj, fps, findings):
    """Scan every annotated tag object on the way from obj to what it points at; returns (sha, type) of the final object."""
    for _ in range(10):
        typ = _git(repo, "cat-file", "-t", obj).strip()
        if typ != "tag":
            return obj, typ
        body = _git(repo, "cat-file", "tag", obj, binary=True)
        f, _ = scan_blob(f"tag object {obj[:12]}", body, fps)
        findings += f
        m = re.match(rb"object ([0-9a-f]{40,64})", body)
        if not m:
            raise Unscannable(f"tag {obj[:12]} without an object line")
        obj = m.group(1).decode()
    raise Unscannable("tags nested too deep")


def scan_git(repo, base, head, fps=(), ref_names=()):
    """Every blob and every path name in the tree of every commit in base..head, merges included, and every commit message: a
    secret added and later removed, or introduced only while resolving a merge, stays in the published history and is found.
    Annotated tags pushed as head are scanned too, and so are the given ref names. A blob is read once, but scanned once per
    way its names would make it be read (a name ending in .zip and one ending in .txt are two readings of the same bytes)."""
    findings, failures = [], []
    for n in ref_names:
        for kind, _ in scan_bytes(n.encode(errors="surrogateescape"), fps):
            findings.append({"path": redact(n, fps), "member": None, "kind": kind + " (in a ref name)", "line": None})
    try:
        head_obj, typ = _peel_tags(repo, _git(repo, "rev-parse", head).strip(), fps, findings)
        if typ != "commit":
            raise Unscannable(f"pushed object is a {typ}, not a commit")
        rng = [head_obj] if base == EMPTY_TREE else [f"{base}..{head_obj}"]
        commits = _git(repo, "rev-list", "--reverse", *rng).split()
        blob_path, names = {}, set()
        for c in commits:
            msg = _git(repo, "log", "-1", "--format=%B", c, binary=True)
            f, _ = scan_blob(f"commit {c[:12]} message", msg, fps)
            findings += f
            for entry in _git(repo, "ls-tree", "-r", "-z", "--full-tree", c, binary=True).split(b"\0"):
                if not entry:
                    continue
                meta, _, path = entry.partition(b"\t")
                mode, typ, sha = meta.split()
                names.add(path)
                if typ == b"blob":
                    label = f"{c[:12]}:{path.decode(errors='surrogateescape')}"
                    blob_path.setdefault(sha.decode(), {}).setdefault(archive_key(label), label)
        for n in sorted(names):
            for kind, _ in scan_bytes(n, fps):
                findings.append({"path": redact(n.decode(errors="surrogateescape"), fps), "member": None,
                                 "kind": kind + " (in a file name)", "line": None})
        shas = sorted(blob_path)
        for i in range(0, len(shas), 500):
            chunk = shas[i:i + 500]
            for sha, data in _blobs(repo, chunk).items():
                for label in blob_path[sha].values():  # one scan per distinct reading of these bytes
                    f, e = scan_blob(label, data, fps)
                    findings += f
                    failures += e
    except Unscannable as exc:
        failures.append({"path": redact(str(repo), fps), "member": None, "error": redact(str(exc), fps)})
    return findings, failures


HEX64 = re.compile(r"[0-9a-f]{64}")


def fingerprint_problems(d, require_kinds=REQUIRED_KINDS):
    """Why a fingerprint document cannot be trusted, or [] if it can. Every entry must be able to match: a 64-digit hexadecimal
    SHA-256, a length of at least MIN_LEN, a prefix of exactly PREFIX characters. Shared by the CLI and the orchestrators."""
    if not isinstance(d, dict) or not isinstance(d.get("fingerprints"), list):
        return ["malformed document"]
    if not isinstance(d.get("unreadable", []), list):
        return ["malformed 'unreadable'"]
    out = []
    for i, f in enumerate(d["fingerprints"]):
        ok = (isinstance(f, dict) and isinstance(f.get("kind"), str) and f["kind"] and isinstance(f.get("source"), str)
              and type(f.get("length")) is int and f["length"] >= MIN_LEN
              and isinstance(f.get("prefix"), str) and len(f["prefix"]) == PREFIX
              and isinstance(f.get("sha256"), str) and HEX64.fullmatch(f["sha256"]))
        if not ok:
            out.append(f"entry {i} cannot match")
    if d.get("unreadable"):
        out.append(f"unreadable stores {[u.get('source') if isinstance(u, dict) else u for u in d['unreadable']]}")
    missing = sorted(set(require_kinds) - {f.get("kind") for f in d["fingerprints"] if isinstance(f, dict)})
    if missing:
        out.append(f"no fingerprint of {missing}")
    return out


def load_fps(path, require_kinds=REQUIRED_KINDS):
    """A fingerprint file that is missing, malformed, lists unreadable stores or lacks a required kind of secret makes the scan
    impossible: exact matching would silently weaken, so the caller must stop (exit 2)."""
    def bad(why):
        sys.stderr.write(f"secret gate: fingerprint file {path}: {why}; refusing to scan\n")
        raise SystemExit(2)
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        bad(type(exc).__name__)
    problems = fingerprint_problems(d, require_kinds)
    if problems:
        bad("; ".join(problems))
    return d["fingerprints"]


# --------------------------------------------------------------------------- redacted copies
FILL = b"*"


def redact_bytes(data, fps=()):
    """Every exact or format match replaced by '*' of the same length (offsets, SQLite pages and record lengths survive)."""
    spans = sorted(_exact_spans(data, fps) + _format_spans(data))
    b = bytearray(data)
    for a, z, _ in spans:
        b[a:z] = FILL * (z - a)
    return bytes(b), len(spans)


def _compression(data):
    for magic, mode in ((b"\x1f\x8b", "gz"), (b"\xfd7zXZ\x00", "xz"), (b"BZh", "bz2")):
        if data.startswith(magic):
            return mode
    return ""


def _json_ok(name, data):
    """True when name says JSON/JSON Lines and data parses as such; None when the name says nothing about JSON."""
    low = name.lower()
    try:
        if low.endswith(".json"):
            json.loads(data.decode("utf-8"))
            return True
        if low.endswith(".jsonl"):
            for line in data.decode("utf-8").splitlines():
                if line.strip():
                    json.loads(line)
            return True
    except (ValueError, UnicodeDecodeError):
        return False
    return None


def _redact_plain(name, data, fps):
    body, n = redact_bytes(data, fps)
    if _json_ok(name, data) and not _json_ok(name, body):
        raise Unscannable("redaction would break JSON")
    return body, n


def _rstr(text, fps):
    b, n = redact_bytes(text.encode(errors="surrogateescape"), fps)
    return b.decode(errors="surrogateescape"), n


def redact_blob(name, data, fps=(), depth=0):
    """(redacted bytes, spans replaced). Plain bytes: same-length replacement (JSON and JSON Lines must stay parseable). Archives
    are rebuilt member by member, with names, link targets, owner names and pax records redacted too, only when they have one
    unambiguous reading: a tar (plain or compressed, the whole decompressed stream), an ordinary zip, or a single compressed
    file. Anything else (data in front of a zip or after a tar, two readings, an unsupported format, two names that redaction
    would make equal) is refused (Unscannable)."""
    if depth > MAX_DEPTH:
        raise Unscannable("archives nested too deep")
    if _open_archive(name, data) is None:  # also raises on anything unreadable or unsupported
        return _redact_plain(name, data, fps)
    comp = _compression(data)
    try:
        members = _tar_members(_tar_stream(data))
        is_tar = True
    except (tarfile.TarError, OSError, EOFError, lzma.LZMAError, ValueError):
        is_tar = False
    is_zip = zipfile.is_zipfile(io.BytesIO(data))
    total, seen = 0, set()

    def unique(n2):
        if n2 in seen:
            raise Unscannable("redaction would make two member names equal")
        seen.add(n2)
        return n2
    if is_tar and not is_zip:
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode=("w:" + comp) if comp else "w", format=tarfile.PAX_FORMAT) as dst:
            for m, body in members:
                n2, k = _rstr(m.name, fps)
                total += k
                info = tarfile.TarInfo(unique(n2))
                for attr in ("mode", "mtime", "type", "uid", "gid"):
                    setattr(info, attr, getattr(m, attr))
                for attr in ("linkname", "uname", "gname"):
                    v, k = _rstr(getattr(m, attr) or "", fps)
                    total += k
                    setattr(info, attr, v)
                pax = {}
                for key, val in (m.pax_headers or {}).items():
                    if key in ("path", "linkpath", "uname", "gname", "size", "mtime", "atime", "ctime"):
                        continue  # rewritten from the fields above
                    k2, a = _rstr(key, fps)
                    v2, b = _rstr(val, fps)
                    total += a + b
                    pax[k2] = v2
                info.pax_headers = pax
                if m.isfile():
                    body2, k = redact_blob(m.name, body, fps, depth + 1)
                    total += k
                    info.size = len(body2)
                    dst.addfile(info, io.BytesIO(body2))
                else:
                    dst.addfile(info)
        return out.getvalue(), total
    if is_zip and not is_tar and not comp and data.startswith(b"PK\x03\x04"):
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
            if src.comment:
                c2, k = redact_bytes(src.comment, fps)
                total += k
                dst.comment = c2
            for i in src.infolist():
                n2, k = _rstr(i.filename, fps)
                total += k
                body, k2 = (b"", 0) if i.is_dir() else redact_blob(i.filename, src.read(i), fps, depth + 1)
                total += k2
                zi = zipfile.ZipInfo(unique(n2), i.date_time)
                zi.compress_type, zi.external_attr = i.compress_type, i.external_attr
                if i.comment:
                    zi.comment, k3 = redact_bytes(i.comment, fps)
                    total += k3
                dst.writestr(zi, body)
        return out.getvalue(), total
    if comp and not is_tar and not is_zip:
        inner = {"gz": gzip.decompress, "xz": lzma.decompress, "bz2": bz2.decompress}[comp](data)
        body, k = redact_blob(name.rsplit(".", 1)[0], inner, fps, depth + 1)
        return {"gz": gzip.compress, "xz": lzma.compress, "bz2": bz2.compress}[comp](body), k
    raise Unscannable("archive with more than one reading: not rebuilt")


def redact_obj(obj, fps=()):
    """A JSON-like structure with every key and string value redacted (records written from private archives)."""
    if isinstance(obj, dict):
        return {redact(str(k), fps) if isinstance(k, str) else k: redact_obj(v, fps) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v, fps) for v in obj]
    if isinstance(obj, str):
        return redact(obj, fps)
    return obj


def redact_paths(paths, outdir, fps=()):
    """Redacted copies of files and folders: input number i goes under outdir/NN/ (inputs never overwrite each other). Every
    destination is planned first and any collision, including one made by redacting names, is refused before anything is
    written; every copy is checked clean afterwards. Returns (written [{path, spans}], failures)."""
    outdir = Path(outdir)
    plan, failures = [], []
    for idx, root in enumerate(paths, 1):
        root = Path(root)
        if not root.exists():
            failures.append({"path": redact(str(root), fps), "member": None, "error": "does not exist"})
            continue
        files = [root] if root.is_file() else _walk_files(root, (), failures, fps)
        for p in files:
            try:
                if not stat.S_ISREG(p.stat().st_mode):
                    continue
            except OSError as exc:
                failures.append({"path": redact(str(p), fps), "member": None, "error": type(exc).__name__})
                continue
            rel = Path(root.name) if p == root else Path(root.name) / p.relative_to(root)
            plan.append((p, Path(f"{idx:02d}") / redact(str(rel), fps)))
    dests = [d for _, d in plan]
    clashes = sorted({str(d) for d in dests if dests.count(d) > 1})
    if clashes:
        return [], failures + [{"path": c, "member": None, "error": "two inputs would be written to the same copy"} for c in clashes]
    if failures:
        return [], failures
    written = []
    for p, rel in plan:
        try:
            body, n = redact_blob(str(p), p.read_bytes(), fps)
        except (OSError, Unscannable) as exc:
            failures.append({"path": str(rel), "member": None, "error": redact(str(exc), fps) or type(exc).__name__})
            continue
        dest = outdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        left, bad = scan_blob(str(dest), body, fps)
        if left or bad:
            failures.append({"path": str(rel), "member": None, "error": "redacted copy is not clean"})
            dest.unlink()
            continue
        written.append({"path": str(rel), "spans": n})
    return written, failures


def report(findings, failures, out=sys.stdout):
    json.dump({"findings": findings, "failures": failures}, out, indent=1)
    out.write("\n")
    return 1 if findings else (2 if failures else 0)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("fingerprints")
    for name in ("scan", "scan-git", "redact"):
        s = sub.add_parser(name)
        g = s.add_mutually_exclusive_group(required=True)
        g.add_argument("--fingerprints", help="fingerprint file taken in the VM (required for anything that will be published)")
        g.add_argument("--formats-only", action="store_true", help="explicit audit without exact matching (e.g. old history)")
        s.add_argument("--require-kinds", default=",".join(REQUIRED_KINDS), help="kinds the fingerprint file must contain")
        if name == "scan":
            s.add_argument("--skip", action="append", default=[], help="folder name to skip (repeatable)")
            s.add_argument("paths", nargs="+")
        elif name == "redact":
            s.add_argument("--out", required=True, help="folder for the redacted copies (must not exist yet)")
            s.add_argument("paths", nargs="+")
        else:
            s.add_argument("--repo", default=".")
            s.add_argument("--ref-name", action="append", default=[], help="ref name to scan too (repeatable)")
            s.add_argument("base")
            s.add_argument("head")
    a = ap.parse_args(argv)
    if a.mode == "fingerprints":
        d = fingerprints()
        json.dump(d, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 2 if d["unreadable"] else 0
    fps = [] if a.formats_only else load_fps(a.fingerprints, [k for k in a.require_kinds.split(",") if k])
    if a.mode == "scan":
        return report(*scan_paths(a.paths, fps, set(a.skip)))
    if a.mode == "redact":
        if Path(a.out).exists():
            sys.stderr.write(f"secret gate: {a.out} exists; refusing to mix redacted copies with other files\n")
            return 2
        written, failures = redact_paths(a.paths, a.out, fps)
        json.dump({"written": written, "failures": failures}, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 2 if failures else 0
    return report(*scan_git(a.repo, a.base, a.head, fps, a.ref_name))


if __name__ == "__main__":
    sys.exit(main())
