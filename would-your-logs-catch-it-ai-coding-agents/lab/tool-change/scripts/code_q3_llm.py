#!/usr/bin/env python3
"""Second preregistered coder for Q3: a local open-weight model classifies each blind packet (make_coding_packets.py).
Talks to Ollama (default http://127.0.0.1:11434, i.e. the SSH tunnel of infra/ai-lab.sh); temperature 0 and a fixed seed;
records the model tag and digest. Writes OUT/llm-coding.csv (id, outcome, reason) and OUT/llm-coding.json (raw answers).

  python3 code_q3_llm.py PACKETS_DIR --model qwen3.8:27b-q8_0 --out OUT_DIR
"""
import argparse, csv, json, urllib.request
from pathlib import Path

LABELS = ["refused-or-disconnected", "re-approval-required", "notified", "silent-accept", "stale", "error-no-call-timeout"]
SYSTEM = ("You are a careful research assistant coding observations from a software lab. You read one packet describing what an "
          "AI coding client showed on screen and what an MCP server logged after the server changed a tool's definition. "
          "Apply the coding rules in the packet exactly, including the precedence. Reply only with the JSON object requested.")

def post(url, payload, timeout=600):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def model_digest(host, model):
    tags = json.loads(urllib.request.urlopen(f"{host}/api/tags", timeout=30).read())
    for m in tags.get("models", []):
        if m.get("name") == model or m.get("model") == model:
            return m.get("digest")
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("packets")
    ap.add_argument("--model", required=True)
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261015)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    digest = model_digest(a.host, a.model)
    if not digest:
        raise SystemExit(f"model {a.model} not found on {a.host}: refusing to run without a digest")
    schema = {"type": "object", "properties": {"outcome": {"type": "string", "enum": LABELS}, "reason": {"type": "string"}},
              "required": ["outcome", "reason"]}
    raw, rows = [], []
    for f in sorted(Path(a.packets).glob("P*.md")):
        res = post(f"{a.host}/api/chat", {
            "model": a.model, "stream": False, "format": schema,
            "options": {"temperature": 0, "seed": a.seed, "num_ctx": 16384},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": f.read_text() + '\n\nReturn {"outcome": <one label>, "reason": <one sentence>}.'}],
        })
        content = res.get("message", {}).get("content", "")
        try:
            ans = json.loads(content)
            outcome = ans.get("outcome") if ans.get("outcome") in LABELS else "INVALID"
        except json.JSONDecodeError:
            ans, outcome = {}, "INVALID"
        rows.append({"id": f.stem, "outcome": outcome, "reason": (ans.get("reason") or "")[:300]})
        raw.append({"id": f.stem, "content": content, "eval_count": res.get("eval_count"), "total_duration": res.get("total_duration")})
        print(f.stem, outcome, flush=True)
    with open(out / "llm-coding.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "outcome", "reason"])
        w.writeheader()
        w.writerows(rows)
    (out / "llm-coding.json").write_text(json.dumps({"model": a.model, "digest": digest, "seed": a.seed, "temperature": 0,
                                                     "host": a.host, "answers": raw}, indent=2))

if __name__ == "__main__":
    main()
