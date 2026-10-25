#!/usr/bin/env python3
"""Additional post hoc coder for Q3 (deviation D22): Codex CLI codes each blind packet in a fresh, isolated session.

Same system instruction, closing request and labels as the local model (code_q3_llm.py); the packet text goes in the prompt;
each session runs in a bubblewrap container (isolated_codex.py) that cannot see the project or the real home directory.
A coding counts only if the isolation probe failed to see the host paths and the initial context passes check_context().
A reference session on a synthetic prompt runs first; its developer blocks are the batch baseline for check_context().
Writes OUT/codex-coding.csv (id, outcome, reason, valid) and OUT/codex-coding.json (reference and per-packet records),
both checkpointed after every packet.

  python3 code_q3_codex.py PACKETS_DIR --auth ~/.codex/auth.json --out OUT_DIR [--model gpt-6.1-sol --effort medium]
"""
import argparse, csv, hashlib, importlib.util, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


llm = _load("code_q3_llm")          # SYSTEM and LABELS: identical to the local model's
iso = _load("isolated_codex")

CLOSING = '\n\nReturn {"outcome": <one label>, "reason": <one sentence>}.'
SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"outcome": {"type": "string", "enum": llm.LABELS}, "reason": {"type": "string"}},
          "required": ["outcome", "reason"]}


def prompt_for(packet_text: str) -> str:
    # The local model received SYSTEM as a system message and the packet + CLOSING as the user message. Codex exec has one
    # prompt, so the same two texts are joined, in that order, with a blank line.
    return llm.SYSTEM + "\n\n" + packet_text + CLOSING


def packet_set_hash(packets):
    lines = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in packets)
    return hashlib.sha256(lines.encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("packets")
    ap.add_argument("--auth", required=True, help="Codex auth.json to copy into each isolated CODEX_HOME")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="gpt-6.1-sol")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--expect-set-hash", help="refuse to run unless the packet set has this SHA-256 (D22)")
    ap.add_argument("--only", help="comma-separated packet ids (smoke tests)")
    a = ap.parse_args()
    packets = sorted(Path(a.packets).glob("P*.md"))
    set_hash = packet_set_hash(packets)
    if a.expect_set_hash and set_hash != a.expect_set_hash:
        sys.exit(f"packet set hash {set_hash} does not match the one declared in D22")
    if a.only:
        keep = set(a.only.split(","))
        packets = [p for p in packets if p.stem in keep]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    ref = iso.reference_session(out / "sessions" / "_reference", Path(a.auth), a.model, a.effort, SCHEMA)
    meta = {"model": a.model, "effort": a.effort, "packet_set_sha256": set_hash, "schema": SCHEMA, "reference": ref, "sessions": []}
    (out / "codex-coding.json").write_text(json.dumps(meta, indent=2))
    if not ref["ok"]:
        sys.exit(f"reference session failed, nothing coded: {ref['context']['problems'] or ref['record'].get('status')}")
    with open(out / "codex-coding.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "outcome", "reason", "valid"])
        w.writeheader()
        for p in packets:
            text = p.read_text()
            prompt = prompt_for(text)
            root = out / "sessions" / p.stem
            rec = iso.run_session(prompt, root, Path(a.auth), a.model, a.effort, SCHEMA)
            outcome, reason = "INVALID", ""
            try:
                ans = json.loads(rec.get("reply") or "")
                if ans.get("outcome") in llm.LABELS:
                    outcome, reason = ans["outcome"], (ans.get("reason") or "")[:300]
            except (json.JSONDecodeError, AttributeError):
                pass
            ctx = (iso.check_context(root / rec["rollouts"][0], prompt, ref["developer_hashes"], ref["environment"]) if rec.get("rollouts")
                   else {"ok": False, "problems": ["no rollout"], "developer_tags": [], "developer_hashes": []})
            valid = rec.get("status") == "completed" and rec["probe"]["ok"] and ctx["ok"] and outcome != "INVALID"
            w.writerow({"id": p.stem, "outcome": outcome if valid else "INVALID", "reason": reason, "valid": valid})
            fh.flush()  # checkpoint after every packet
            meta["sessions"].append({"id": p.stem, "packet_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                                     "status": rec.get("status"), "exit_code": rec.get("exit_code"), "probe": rec["probe"],
                                     "context": ctx, "valid": valid, "raw_reply": rec.get("reply"), "rollouts": rec.get("rollouts", [])})
            (out / "codex-coding.json").write_text(json.dumps(meta, indent=2))
            print(p.stem, outcome, "valid" if valid else "INVALID", flush=True)


if __name__ == "__main__":
    main()
