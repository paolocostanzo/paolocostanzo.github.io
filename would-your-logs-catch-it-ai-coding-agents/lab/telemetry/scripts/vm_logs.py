#!/usr/bin/env python3
"""Runs INSIDE the disposable VM (Python 3, standard library only). Read-only on the client stores.

  python3 vm_logs.py inventory CANDIDATES.json            # which candidate log files exist now -> JSON on stdout
  python3 vm_logs.py collect CANDIDATES.json START END    # tar.gz of every matching file on stdout; manifest as the
                                                          # first member (manifest.json)

START and END are Unix seconds of the session (D24 point 2). Paths in the manifest are relative to the home directory.
The manifest records, for every client and every frozen candidate pattern, how many files matched (so that a location that was
looked at and found empty is distinguishable from one that was not looked at), every file with size, mtime, SHA-256 and whether
it was modified inside [START, END], the files that could not be read, the SHA-256 of the candidates file, and the client's
logging configuration at collection time (hashes of its settings files and the names of logging variables in the environment).
SQLite databases are copied together with their -wal and -shm files. Each database is also opened from a private copy (never the
original), checked, and written as a consolidated copy under sqlite-consolidated/ with its tables' row counts, so that records
still in the write-ahead log are not lost. For JSON Lines and SQLite files the range of record timestamps is recorded where a
timestamp field can be recognised; otherwise the range is reported as unknown. The clients must be stopped before collecting.
"""
import glob, hashlib, io, json, os, re, shutil, sqlite3, sys, tarfile, tempfile, time
from datetime import datetime

HOME = os.path.expanduser("~")
SIDECARS = ("-wal", "-shm", "-journal")
SQLITE_EXT = (".sqlite", ".sqlite3", ".db")
# settings files that can change what a client logs (D24 point 2); hashed, never copied
CONFIG_FILES = {"claude-code": ["~/.claude/settings.json"], "codex": ["~/.codex/config.toml"], "gemini": ["~/.gemini/settings.json"],
                "qwen": ["~/.qwen/settings.json"], "goose": ["~/.config/goose/config.yaml"], "cursor-agent": ["~/.cursor/cli-config.json"]}
LOG_ENV = re.compile(r"(LOG|DEBUG|VERBOSE|TRACE|TELEMETRY|OTEL)", re.I)
TS_KEYS = ("timestamp", "ts", "time", "created_at", "createdAt", "date")
TS_COLS = re.compile(r"^(ts|timestamp|time|created_at|createdat|updated_at|date)$", re.I)


def expand(pattern):
    p = os.path.expanduser(pattern)
    return sorted(f for f in glob.glob(p, recursive=True) if os.path.isfile(f) and not os.path.islink(f))


def static_prefix(pattern):
    """The part of a pattern before its first wildcard component, e.g. ~/.claude/projects for ~/.claude/projects/**/*.jsonl."""
    parts = []
    for comp in os.path.expanduser(pattern).split(os.sep):
        if any(ch in comp for ch in "*?["):
            break
        parts.append(comp)
    return os.sep.join(parts) or os.sep


def inspect_location(pattern):
    """'absent' when the fixed part of the pattern does not exist; otherwise 'present' with every error met while walking it
    (glob silently skips directories it cannot read, so an unreadable location would look empty without this check)."""
    base = static_prefix(pattern)
    try:
        os.lstat(base)
    except (FileNotFoundError, NotADirectoryError):
        return {"location": "absent", "errors": []}  # genuinely absent: every ancestor could be searched
    except OSError as exc:  # e.g. EACCES on an ancestor: the location cannot be inspected, which is not "absent"
        return {"location": "present", "errors": [f"{os.path.relpath(base, HOME)}: {exc.strerror}"]}
    errors = []
    if os.path.isdir(base):
        if not os.access(base, os.R_OK | os.X_OK):
            errors.append(f"{os.path.relpath(base, HOME)}: not readable")
        for root, dirs, files in os.walk(base, onerror=lambda e: errors.append(f"{os.path.relpath(e.filename, HOME)}: {e.strerror}")):
            for f in files:
                fp = os.path.join(root, f)
                if not os.path.islink(fp) and not os.access(fp, os.R_OK):
                    errors.append(f"{os.path.relpath(fp, HOME)}: not readable")
    elif not os.access(base, os.R_OK):
        errors.append(f"{os.path.relpath(base, HOME)}: not readable")
    return {"location": "present", "errors": errors}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_epoch(v):
    """A record timestamp as Unix seconds, from epoch seconds or milliseconds or an ISO 8601 string; None if not recognised."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v / 1000 if v > 1e11 else v if v > 1e8 else None
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def jsonl_range(path, limit=200000):
    lo = hi = None
    n = 0
    with open(path, "rb") as fh:
        for n, line in enumerate(fh, 1):
            if n > limit:
                break
            try:
                rec = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict):
                continue
            for k in TS_KEYS:
                t = to_epoch(rec.get(k))
                if t is not None:
                    lo, hi = (t if lo is None else min(lo, t)), (t if hi is None else max(hi, t))
                    break
    return {"first": lo, "last": hi} if lo is not None else "unknown"


def json_range(path, max_bytes=50 << 20):
    """Record times of a JSON file holding a list of records (Gemini CLI's and Qwen Code's logs.json)."""
    if os.path.getsize(path) > max_bytes:
        return "unknown"
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (ValueError, UnicodeDecodeError, OSError):
        return "unknown"
    times = []
    for rec in data if isinstance(data, list) else []:
        if isinstance(rec, dict):
            for k in TS_KEYS:
                e = to_epoch(rec.get(k))
                if e is not None:
                    times.append(e)
                    break
    return {"first": min(times), "last": max(times)} if times else "unknown"


def sqlite_copy_and_check(path, workdir):
    """Copy a database with its sidecars to workdir, open the copy, and write a consolidated copy (WAL folded in)."""
    base = os.path.join(workdir, "db")
    shutil.copyfile(path, base)
    for s in SIDECARS:
        if os.path.exists(path + s):
            shutil.copyfile(path + s, base + s)
    out = os.path.join(workdir, "consolidated")
    info = {"ok": False}
    try:
        src = sqlite3.connect(base)
        info["quick_check"] = src.execute("PRAGMA quick_check").fetchone()[0]
        dst = sqlite3.connect(out)
        src.backup(dst)
        tables = [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        info["tables"] = {t: dst.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
        lo = hi = None
        for t in tables:
            for col in [c[1] for c in dst.execute(f'PRAGMA table_info("{t}")') if TS_COLS.match(c[1])]:
                for v in dst.execute(f'SELECT min("{col}"), max("{col}") FROM "{t}"').fetchone():
                    e = to_epoch(v)
                    if e is not None:
                        lo, hi = (e if lo is None else min(lo, e)), (e if hi is None else max(hi, e))
        info["record_range"] = {"first": lo, "last": hi} if lo is not None else "unknown"
        dst.close(); src.close()
        info["ok"] = info["quick_check"] == "ok"
    except sqlite3.Error as exc:
        info["error"] = str(exc)
        out = None
    return info, out


def describe(path, start=None, end=None):
    st = os.stat(path)
    d = {"path": os.path.relpath(path, HOME), "size": st.st_size, "mtime": int(st.st_mtime), "sha256": sha256(path)}
    if start is not None:
        d["modified_in_session"] = start <= st.st_mtime <= end + 5
    return d


def clients_of(candidates):
    return {c: p for c, p in candidates.items() if not c.startswith("_")}


def inventory(candidates):
    out = {}
    for client, patterns in clients_of(candidates).items():
        files = []
        for pat in patterns:
            files += [describe(f) | {"pattern": pat} for f in expand(pat)]
        out[client] = {"patterns": patterns, "files": files}
    return {"taken_at": int(time.time()), "home": HOME, "clients": out}


def logging_config(client):
    files = {}
    for f in CONFIG_FILES.get(client, []):
        p = os.path.expanduser(f)
        files[f] = sha256(p) if os.path.isfile(p) else None
    return {"settings_sha256": files, "env_logging_variables": sorted(k for k in os.environ if LOG_ENV.search(k))}


def collect(candidates, start, end, stream, candidates_sha256=None):
    manifest = {"start": start, "end": end, "taken_at": int(time.time()), "candidates_sha256": candidates_sha256,
                "clients": {}, "patterns": {}, "errors": [], "sqlite": {}, "record_ranges": {}, "logging_config": {}}
    members = []  # (path on disk, name in archive)
    work = tempfile.mkdtemp(prefix="q4-collect-")
    try:
        for client, patterns in clients_of(candidates).items():
            manifest["patterns"][client] = []
            files = set()
            for pat in patterns:
                found = expand(pat)
                loc = inspect_location(pat)
                manifest["patterns"][client].append({"pattern": pat, "matches": len(found), "location": loc["location"],
                                                     "errors": len(loc["errors"])})
                manifest["errors"] += [{"client": client, "pattern": pat, "error": e} for e in loc["errors"]]
                files |= set(found)
            for f in sorted(files):  # sidecars of databases travel with them
                if f.endswith(SQLITE_EXT):
                    files |= {f + s for s in SIDECARS if os.path.isfile(f + s)}
            entries = []
            for f in sorted(files):
                rel = os.path.relpath(f, HOME)
                try:
                    entries.append(describe(f, start, end))
                except OSError as exc:
                    manifest["errors"].append({"client": client, "path": rel, "error": str(exc)})
                    continue
                members.append((f, "home/" + rel))
                if f.endswith(".jsonl"):
                    manifest["record_ranges"][rel] = jsonl_range(f)
                elif f.endswith(".json"):
                    manifest["record_ranges"][rel] = json_range(f)
                if f.endswith(SQLITE_EXT):
                    d = tempfile.mkdtemp(dir=work)
                    info, cons = sqlite_copy_and_check(f, d)
                    manifest["sqlite"][rel] = info
                    manifest["record_ranges"][rel] = info.get("record_range", "unknown")
                    if cons:
                        members.append((cons, "sqlite-consolidated/" + rel))
            manifest["clients"][client] = entries
            manifest["logging_config"][client] = logging_config(client)
        with tarfile.open(fileobj=stream, mode="w:gz") as tar:
            data = json.dumps(manifest, indent=1).encode()
            info = tarfile.TarInfo("manifest.json"); info.size = len(data); info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
            for f, name in members:
                tar.add(f, arcname=name, recursive=False)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return manifest


def main():
    mode, cand_path = sys.argv[1], sys.argv[2]
    raw = open(cand_path, "rb").read()
    cand = json.loads(raw)
    if mode == "inventory":
        json.dump(inventory(cand), sys.stdout, indent=1)
    elif mode == "collect":
        collect(cand, int(sys.argv[3]), int(sys.argv[4]), sys.stdout.buffer, hashlib.sha256(raw).hexdigest())
    else:
        sys.exit("mode must be inventory or collect")


if __name__ == "__main__":
    main()
