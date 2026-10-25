#!/usr/bin/env python3
"""The records of one client's default logs in a run or session, as a time-ordered list (D27 points 5-6).

Input: the redacted client-log archive of a run or session (client-logs.redacted.tgz, made by vm_logs.py and the secret gate of
D26) and the client's frozen log locations (inventory/candidates.json). Only the files under the client's own locations are read.
Each record is {"t": Unix seconds or None, "src": the file's path under the home directory, "line": its number or row, "text":
the record as text}. Record times are read as vm_logs.py reads them (TS_KEYS, TS_COLS, to_epoch):
  - JSON Lines: one record per line;
  - JSON holding a list (Gemini CLI's and Qwen Code's logs.json): one record per item;
  - SQLite: one record per row of every table, from the consolidated copy (WAL folded in); the time is the first time-like column;
  - plain text: one record per line (note: a "*" in a location matches "/" too, so nested transcripts are read); a line holding a JSON object gives its time as above, otherwise a leading ISO 8601 time is
    read, otherwise the time is None.
A file with no timed record has all its records dated by its modification time in the archive ("time_from": "mtime"); an ISO
time without a zone is read as UTC; Codex's ts_nanos is added to ts. A record
without a time cannot be placed in a one-minute window or after an event: rules never use it (apply.py).

  python3 normalize.py ARCHIVE CLIENT [--start T0 --end T1]    # prints the records as JSON Lines
"""
import argparse, fnmatch, importlib.util, json, re, sqlite3, sys, tarfile, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEL = HERE.parent
_spec = importlib.util.spec_from_file_location("vm_logs", TEL / "scripts" / "vm_logs.py")
vm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vm)
CANDIDATES = TEL / "inventory" / "candidates.json"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")  # colour codes in front of a time (Codex's daemon logs; Codex review, round 1, R2)
LEADING_TIME = re.compile(r"^\W*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")


def patterns(client):
    """The client's frozen locations, relative to the home directory."""
    return [p[2:] if p.startswith("~/") else p for p in json.load(open(CANDIDATES))[client]]


def matches(rel, pats):
    for p in pats:
        # "**/" also matches no directory at all, as the exporter's glob does
        if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, p.replace("**/", "")):
            return True
        if p.endswith("/**") and rel.startswith(p[:-3] + "/"):
            return True
    return False


def to_epoch(v):
    """vm_logs.to_epoch, except that an ISO 8601 time without a time zone is read as UTC, not as the host's local time: Goose's
    sessions.db stores UTC times without a zone (exploration of 22 Oct 2026); every other client writes "Z" or an offset."""
    if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?$", v.strip()):
        v = v.strip() + "+00:00"
    return vm.to_epoch(v)


def first_time(rec):
    for k in vm.TS_KEYS:
        t = to_epoch(rec.get(k))
        if t is not None:
            return t
    return None


def jsonl_records(data, src):
    for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        try:
            rec = json.loads(line)
        except ValueError:
            yield {"t": None, "src": src, "line": n, "text": line}
            continue
        yield {"t": first_time(rec) if isinstance(rec, dict) else None, "src": src, "line": n, "text": line}


def json_records(data, src):
    try:
        items = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return
    for n, rec in enumerate(items if isinstance(items, list) else [], 1):
        yield {"t": first_time(rec) if isinstance(rec, dict) else None, "src": src, "line": n,
               "text": json.dumps(rec, ensure_ascii=False, sort_keys=True)}


def sqlite_records(data, src):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite"
        path.write_bytes(data)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
            for table in tables:
                cur = con.execute(f'select * from "{table}"')
                cols = [d[0] for d in cur.description]
                tcol = next((i for i, c in enumerate(cols) if vm.TS_COLS.match(c)), None)
                ncol = cols.index("ts_nanos") if "ts_nanos" in cols else None  # Codex: whole seconds in ts, the rest here
                for n, row in enumerate(cur, 1):
                    rec = {c: (v.decode("utf-8", "replace") if isinstance(v, bytes) else v) for c, v in zip(cols, row)}
                    t = to_epoch(row[tcol]) if tcol is not None else None
                    if t is not None and ncol is not None and isinstance(row[ncol], int) and isinstance(row[tcol], int):
                        t = row[tcol] + row[ncol] / 1e9
                    yield {"t": t, "src": f"{src}#{table}", "line": n, "text": json.dumps(rec, ensure_ascii=False, default=str)}
        finally:
            con.close()


def text_records(data, src):
    """Plain-text logs; a line that is a JSON object (Goose's .log files) gives its time like a JSON Lines record."""
    for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        t = None
        if line.lstrip().startswith("{"):
            try:
                rec = json.loads(line)
                t = first_time(rec) if isinstance(rec, dict) else None
            except ValueError:
                pass
        if t is None:
            m = LEADING_TIME.match(ANSI.sub("", line))
            t = to_epoch(m.group(1)) if m else None
        yield {"t": t, "src": src, "line": n, "text": line}


def records(archive, client, start=None, end=None):
    """The client's records, time-ordered (records without a time last), optionally limited to [start, end]."""
    pats = patterns(client)
    out = []
    with tarfile.open(archive, mode="r:gz") as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}
        for name, m in members.items():
            if not name.startswith("home/"):
                continue
            rel = name[len("home/"):]
            if not matches(rel, pats) or rel.endswith(("-wal", "-shm")):
                continue
            consolidated = "sqlite-consolidated/" + rel
            if consolidated not in members and f"{name}-wal" in members:
                # without the consolidated copy the base file lacks the rows still in its write-ahead log: refuse, never read
                # a database partly (adversarial review of the candidates, R5)
                raise SystemExit(f"{rel}: write-ahead log without a consolidated copy in {archive}")
            src_member = members[consolidated] if consolidated in members else m
            data = tar.extractfile(src_member).read()
            if data[:16] == b"SQLite format 3\x00":
                gen = sqlite_records(data, rel)
            elif rel.endswith(".jsonl"):
                gen = jsonl_records(data, rel)
            elif rel.endswith(".json"):
                gen = json_records(data, rel)
            else:
                gen = text_records(data, rel)
            recs = list(gen)
            if not any(r["t"] is not None for r in recs):
                # a file with no timed record (Qwen Code's runtime.json, Goose's llm_request files): every record is dated by the
                # file's modification time, as the exporter copied it (VM clock), and marked so that a rule knows the time is the
                # file's, not a record's; a file that gave no record at all gives one record with its whole text
                for r in recs:
                    r["t"], r["time_from"] = float(src_member.mtime), "mtime"
                if not recs:
                    recs.append({"t": float(src_member.mtime), "src": rel, "line": 0, "time_from": "mtime",
                                 "text": data.decode("utf-8", "replace") if data[:16] != b"SQLite format 3\x00" else ""})
            out.extend(recs)
    if start is not None or end is not None:
        out = [r for r in out if r["t"] is not None and (start is None or r["t"] >= start) and (end is None or r["t"] <= end)]
    return sorted(out, key=lambda r: (r["t"] is None, r["t"] or 0, r["src"], r["line"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("archive")
    ap.add_argument("client")
    ap.add_argument("--start", type=float)
    ap.add_argument("--end", type=float)
    a = ap.parse_args()
    for r in records(a.archive, a.client, a.start, a.end):
        sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
