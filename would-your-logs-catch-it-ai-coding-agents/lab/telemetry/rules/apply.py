#!/usr/bin/env python3
"""Apply frozen detection rules to one client's default logs (D27 points 5-6): the times at which each rule fires.

A rule file is JSON: {"rules": {"<id>": {"client": ..., "event": "q5-start|q3-announced|q3-silent", "class": "<what kind of event
it describes>", "type": ..., ...}}}. Rule types:
  - "match":      {"source": glob, "regex": re} fires at every timed record of a source matching the regex.
  - "changed":    {"source": glob, "extract": name} fires when a tool's definition, as the extractor reads it, differs from the
                  last earlier definition of the same tool in the same extractor stream (earlier records of the archive count,
                  history included).
  - "unannounced": {"changed": rule id, "announced": rule id} fires at a "changed" hit with no "announced" hit between the
                  earlier and the later observation of the definition.
  - "new-name":   {"source": glob, "regex": re with a group "name", optional "field": "src" (match the file path instead of
                  the record), optional "after": re and "within_s": seconds} fires when a name is captured that no earlier
                  record of the source carried, and, if "after" is given, a record of the same source matching it lies within
                  within_s seconds before.
  - "none":       {"reason": text} never fires: no default-log record tells this event apart (the pair is then not
                  distinguishable).
Records come from normalize.py. A record dated by its file's modification time ("time_from": "mtime") is used only by a rule
that says "allow_mtime": true. Only hits inside [start, end] are returned; earlier records are read, as history.
Lab literals (D27 point 6) are refused in a rule's patterns (check_literals).

  python3 apply.py RULES.json ARCHIVE CLIENT START END       # prints {"<rule id>": [hit times]} for that client's rules
"""
import fnmatch, hashlib, importlib.util, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("normalize", HERE / "normalize.py")
nz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nz)
# D27 point 6: the lab's names, paths, addresses and the text of the lab tool's definitions (tool-change/server/q3-server.mjs,
# trusting-a-repo/canary/marker-server.mjs); separators between words are matched loosely so that "q3.lab" is refused too
FORBIDDEN = re.compile(r"q3\W?_?lab|q3\W?_?noop|q3\W?_?repo|q5\W?_?repo|q5\W?_?canary|canary|q4\W?_?out|/home/lab|"
                       r"192\.168\.122\.|Lab tool, revision|records the call in its own log", re.I)
EXTRACTORS = {}


def extractor(name):
    def wrap(fn):
        EXTRACTORS[name] = fn
        return fn
    return wrap


def canon(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def project(rec):
    """The project a transcript or chat belongs to (.claude/projects/<p>/..., .qwen/projects/<p>/...), so that a tool name is
    compared only with earlier definitions in the same project (adversarial review of the candidates, R3)."""
    parts = rec["src"].split("/")
    return parts[2] if len(parts) > 2 and parts[1] == "projects" else ""


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


@extractor("claude-tools")
def claude_tools(rec):
    """Claude Code transcripts: MCP tool definitions in api-request-shape (shape.tools) and in deferred_tools_record entries."""
    try:
        r = json.loads(rec["text"])
    except ValueError:
        return []
    out = []
    if isinstance(r, dict) and r.get("type") == "api-request-shape":
        for t in ((r.get("shape") or {}).get("tools") or []):
            if isinstance(t, dict) and str(t.get("name", "")).startswith("mcp__"):
                out.append((("shape", project(rec), t["name"]), canon({k: t.get(k) for k in ("description", "input_schema")})))
    att = r.get("attachment") if isinstance(r, dict) else None
    if isinstance(att, dict) and att.get("type") == "deferred_tools_record":
        for t in att.get("entries") or []:
            if isinstance(t, dict) and str(t.get("name", "")).startswith("mcp__"):
                out.append((("deferred", project(rec), t["name"]), canon({k: t.get(k) for k in ("description", "input_schema")})))
    return out


FUNCTION_BLOCK = re.compile(r"<function>(\{.*?\})</function>", re.S)


@extractor("qwen-tool-search")
def qwen_tool_search(rec):
    """Qwen Code chats: tool definitions returned by its tool_search tool, as <function>{json}</function> blocks in the output of
    a functionResponse named tool_search (Codex review of the rules, round 1, R1: only that response, nothing else)."""
    try:
        r = json.loads(rec["text"])
    except ValueError:
        return []
    if not (isinstance(r, dict) and r.get("type") == "tool_result"):
        return []
    out = []
    for part in ((r.get("message") or {}).get("parts") or []):
        resp = part.get("functionResponse") if isinstance(part, dict) else None
        if not (isinstance(resp, dict) and resp.get("name") == "tool_search"):
            continue
        output = (resp.get("response") or {}).get("output")
        for block in FUNCTION_BLOCK.findall(output if isinstance(output, str) else ""):
            try:
                f = json.loads(block)
            except ValueError:
                continue
            if isinstance(f, dict) and str(f.get("name", "")).startswith("mcp__"):
                out.append((("search", project(rec), f["name"]), canon({k: f.get(k) for k in ("description", "parametersJsonSchema")})))
    return out


@extractor("goose-request-tools")
def goose_request_tools(rec):
    """Goose llm_request files: input.tools.functionDeclarations[*] of a model request."""
    try:
        r = json.loads(rec["text"])
    except ValueError:
        return []
    decls = (((r.get("input") or {}).get("tools") or {}).get("functionDeclarations") or []) if isinstance(r, dict) else []
    if isinstance(decls, dict):
        decls = [decls]
    return [(("request", d["name"]), canon(d)) for d in decls if isinstance(d, dict) and d.get("name")]


def check_literals(rules):
    """Refuse a rule that names the lab anywhere in its definition (D27 point 6): every field is scanned, as JSON. A name hidden in
    regular-expression syntax (for example "q3\\Wlab") is not caught here: the adversarial and Codex reviews of the rules cover it."""
    bad = [rid for rid, r in rules.items() if FORBIDDEN.search(json.dumps(r, ensure_ascii=False))]
    if bad:
        raise SystemExit(f"rules using lab literals (D27 point 6): {bad}")


def usable(rec, rule):
    return rec["t"] is not None and (rec.get("time_from") != "mtime" or rule.get("allow_mtime"))


def from_source(recs, rule):
    return [r for r in recs if fnmatch.fnmatch(r["src"].split("#")[0], rule["source"]) and usable(r, rule)]


def run_rule(rid, rules, recs, cache):
    if rid in cache:
        return cache[rid]
    rule = rules[rid]
    hits = []  # (time, earlier observation time or None)
    if rule["type"] == "match":
        rx = re.compile(rule["regex"])
        hits = [(r["t"], None) for r in from_source(recs, rule) if rx.search(r["text"])]
    elif rule["type"] == "changed":
        ex = EXTRACTORS[rule["extract"]]
        last = {}
        for r in from_source(recs, rule):
            for key, value in ex(r):
                if key in last and last[key][0] != value:
                    hits.append((r["t"], last[key][1]))
                last[key] = (value, r["t"])
    elif rule["type"] == "unannounced":
        changed = run_rule(rule["changed"], rules, recs, cache)
        announced = [t for t, _ in run_rule(rule["announced"], rules, recs, cache)]
        hits = [(t, prev) for t, prev in changed if not any(prev <= a <= t for a in announced)]
    elif rule["type"] == "new-name":
        rx = re.compile(rule["regex"])
        after = re.compile(rule["after"]) if rule.get("after") else None
        seen, precursors = set(), []
        for r in from_source(recs, rule):
            if after and after.search(r["text"]):
                precursors.append(r["t"])
            m = rx.search(r["src"] if rule.get("field") == "src" else r["text"])
            if not m:
                continue
            name = m.group("name")
            if name not in seen and (after is None or any(0 <= r["t"] - p <= rule.get("within_s", 10) for p in precursors)):
                hits.append((r["t"], None))
            seen.add(name)
    elif rule["type"] == "none":
        hits = []
    else:
        raise ValueError(f"unknown rule type {rule['type']}")
    cache[rid] = sorted(hits)
    return cache[rid]


def coverage(rules, archive, client, start, end, size=60):
    """For each rule of the client: how many one-minute windows of [start, end] hold at least one usable record of the rule's
    source. A window without any record cannot produce a false alarm; the report gives this next to the false-positive count
    (adversarial review of the candidates, R2: Goose keeps only its last ten model requests)."""
    recs = nz.records(archive, client, start, end)
    n = int((end - start) // size)
    out = {}
    for rid, r in rules.items():
        if r["client"] != client:
            continue
        if r["type"] in ("none", "unannounced"):
            out[rid] = None
            continue
        out[rid] = len({int((x["t"] - start) // size) for x in from_source(recs, r) if x["t"] < start + n * size})
    return out


def apply(rules, archive, client, start, end):
    """{rule id: [hit times in [start, end]]} for the rules of this client."""
    check_literals(rules)
    recs = nz.records(archive, client)  # the whole archive: earlier records are history
    cache = {}
    return {rid: [t for t, _ in run_rule(rid, rules, recs, cache) if start <= t <= end]
            for rid, r in rules.items() if r["client"] == client}


if __name__ == "__main__":
    rules = json.load(open(sys.argv[1]))["rules"]
    print(json.dumps(apply(rules, sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5])), indent=1))
