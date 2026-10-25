#!/usr/bin/env python3
"""Collection check for one session (D24 point 2), independent of detection.

Inputs: the archive written by `vm_logs.py collect` (host copy), the client that ran, the session window, the inventory taken on
the clean snapshot (frozen patterns per client) and the SHA-256 of the frozen candidates file. Output: one JSON record with
  export: ok | failed. Failed when the archive or its manifest cannot be read; the manifest lacks the client, does not account
          for every frozen pattern of the client, or was made from another candidates file; a listed file is missing from the
          archive or its hash differs; a file could not be read in the VM; a database could not be opened from its copy, or its
          consolidated copy is missing.
  client_logs: wrote | wrote-nothing-in-session | no-default-logs
          wrote = a file was modified in the session window, or a record with a timestamp inside it was exported;
          no-default-logs = no frozen location of the client exists, in the inventory or in this export.
  record_coverage: per exported file, whether its records' timestamps reach into the session window, or "unknown" when the file
          has no recognisable timestamps; and the session's logging configuration as recorded in the VM.
Coverage with respect to the event itself is computed in the analysis, from the ground-truth time (score.py), never here.
The full record names client files, and some paths name services connected to the lab account (for example Claude Code's
per-server MCP logs): it stays in the private evidence. public_record() gives the version for the public evidence: counts per
frozen pattern, no file path, and failure reasons reduced to their category.
A session is excluded only when export is "failed"; a client that logs nothing for the event is a valid negative observation.

  python3 collection_check.py ARCHIVE.tgz CLIENT START END --inventory INVENTORY.json --candidates-sha256 HEX
"""
import argparse, hashlib, json, re, sys, tarfile


def failed(rec, reason):
    return rec | {"export": "failed", "reason": reason}


def in_window(rng, start, end):
    if not isinstance(rng, dict) or rng.get("first") is None:
        return "unknown"
    return rng["first"] <= end + 5 and rng["last"] >= start


def check(archive_path, client, start, end, inventory, candidates_sha256):
    rec = {"client": client, "start": start, "end": end}
    try:
        tar = tarfile.open(archive_path, "r:gz")
        manifest = json.load(tar.extractfile("manifest.json"))
        members = {m.name: m for m in tar.getmembers()}
    except (OSError, tarfile.TarError, KeyError, ValueError, TypeError) as exc:
        return failed(rec, f"archive or manifest unreadable: {exc}")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("clients"), dict) or not isinstance(manifest.get("patterns"), dict):
        return failed(rec, "manifest without clients/patterns")
    if manifest.get("candidates_sha256") != candidates_sha256:
        return failed(rec, f"manifest made from another candidates file: {manifest.get('candidates_sha256')}")
    if client not in manifest["clients"] or client not in manifest["patterns"]:
        return failed(rec, f"manifest has no entry for {client}")
    frozen = inventory["clients"][client]["patterns"]
    looked = {p.get("pattern") for p in manifest["patterns"][client] if p.get("location") in ("absent", "present")}
    if set(frozen) - looked:
        return failed(rec, f"manifest does not account for {sorted(set(frozen) - looked)}")
    errs = [e for e in manifest.get("errors", []) if e.get("client") == client]
    if errs:
        return failed(rec, f"files not readable in the VM: {errs[:3]}")
    files = manifest["clients"][client]
    for f in files:
        m = members.get("home/" + f["path"])
        if m is None:
            return failed(rec, f"missing from archive: {f['path']}")
        if hashlib.sha256(tar.extractfile(m).read()).hexdigest() != f["sha256"]:
            return failed(rec, f"hash mismatch: {f['path']}")
        db = manifest.get("sqlite", {}).get(f["path"])
        if db is not None:
            if not db.get("ok"):
                return failed(rec, f"database not readable from its copy: {f['path']}: {db.get('error') or db.get('quick_check')}")
            if "sqlite-consolidated/" + f["path"] not in members:
                return failed(rec, f"consolidated copy missing: {f['path']}")
    ranges = manifest.get("record_ranges", {})
    cov, pattern_of = {}, {}
    for f in files:
        base = next((f["path"][:-len(s)] for s in ("-wal", "-shm", "-journal") if f["path"].endswith(s)), f["path"])
        # every exported file gets a coverage value: its own record range, its database's for a sidecar, else "unknown"
        cov[f["path"]] = in_window(ranges.get(f["path"], ranges.get(base)), start, end)
        pattern_of[f["path"]] = [p for p in frozen if fnmatch_home(base, p)]  # which frozen pattern(s), for the public counts
    modified = [f for f in files if start <= f["mtime"] <= end + 5]
    inv_files = inventory["clients"][client].get("files")
    if not inv_files and not files:
        status = "no-default-logs"
    elif modified or any(v is True for v in cov.values()):
        status = "wrote"
    else:
        status = "wrote-nothing-in-session"
    return rec | {"export": "ok", "client_logs": status, "files": len(files), "files_modified_in_session": len(modified),
                  "record_coverage": {"files_with_records_in_session": sum(v is True for v in cov.values()),
                                      "files_with_known_range": sum(v != "unknown" for v in cov.values()),
                                      "files_without_timestamps": sum(v == "unknown" for v in cov.values()), "per_file": cov},
                  "patterns": manifest["patterns"][client], "logging_config": manifest.get("logging_config", {}).get(client),
                  "pattern_of": pattern_of}


def fnmatch_home(rel, pattern):
    """Whether a home-relative path matches a ~/ pattern: ** = any number of directories (anything, when last), * and ? within
    one component, as glob.glob(recursive=True) reads them."""
    parts = (pattern[2:] if pattern.startswith("~/") else pattern).split("/")
    rx = ""
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part == "**":
            rx += ".*" if last else "(?:[^/]+/)*"
        else:
            rx += re.escape(part).replace(r"\*", "[^/]*").replace(r"\?", "[^/]") + ("" if last else "/")
    return re.fullmatch(rx, rel) is not None


def public_record(rec):
    """The collection record without file paths: per frozen pattern, files exported and files with records in the session."""
    out = {k: rec[k] for k in ("client", "start", "end", "export", "client_logs", "files", "files_modified_in_session") if k in rec}
    if "reason" in rec:
        out["reason"] = rec["reason"].split(":")[0]
    per = rec.get("record_coverage", {}).get("per_file", {})
    counts = []
    for p in rec.get("patterns", []):
        mine = [f for f, pats in rec.get("pattern_of", {}).items() if p["pattern"] in pats]
        counts.append({"pattern": p["pattern"], "location": p.get("location"), "files": len(mine),
                       "with_records_in_session": sum(per.get(f) is True for f in mine),
                       "without_timestamps": sum(per.get(f) == "unknown" for f in mine)})
    if counts:
        out["patterns"] = counts
    if rec.get("logging_config"):
        out["logging_config"] = rec["logging_config"]  # generic settings paths and variable names only
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archive"); ap.add_argument("client"); ap.add_argument("start", type=int); ap.add_argument("end", type=int)
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--candidates-sha256", required=True)
    a = ap.parse_args()
    json.dump(check(a.archive, a.client, a.start, a.end, json.load(open(a.inventory)), a.candidates_sha256), sys.stdout, indent=1)


if __name__ == "__main__":
    main()
