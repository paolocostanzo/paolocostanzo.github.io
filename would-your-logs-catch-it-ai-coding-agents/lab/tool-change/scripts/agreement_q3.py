#!/usr/bin/env python3
"""Agreement between the two preregistered Q3 coders (Cohen's kappa), and of each with the rule-based coder.

  python3 agreement_q3.py AUTHOR.csv LLM.csv KEY.csv RESULTS_DIR
AUTHOR.csv and LLM.csv: id,outcome[,...]; KEY.csv: id,repetition (from make_coding_packets.py).
Kappa is undefined (reported as null) when expected agreement is 1. Unlabelled or invalid answers are counted and excluded.
"""
import csv, json, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import code_q3  # noqa: E402

def read(path):
    return {r["id"]: (r.get("outcome") or "").strip() for r in csv.DictReader(open(path, newline=""))}

def kappa(a, b):
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)

def pair(x, y):
    ids = sorted(i for i in x if i in y and x[i] and y[i] and x[i] != "INVALID" and y[i] != "INVALID")
    a, b = [x[i] for i in ids], [y[i] for i in ids]
    return {"n": len(ids), "agreement": (sum(p == q for p, q in zip(a, b)) / len(ids)) if ids else None, "kappa": kappa(a, b),
            "disagreements": [{"id": i, "first": x[i], "second": y[i]} for i in ids if x[i] != y[i]],
            "excluded": sorted((set(x) | set(y)) - set(ids))}

def main():
    author, llm, key, results = read(sys.argv[1]), read(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
    rule = {}
    for r in csv.DictReader(open(key, newline="")):
        rule[r["id"]] = code_q3.code(results / r["repetition"]).get("p2_outcome", "")
    out = {"author_vs_llm (preregistered kappa)": pair(author, llm), "author_vs_rule": pair(author, rule), "llm_vs_rule": pair(llm, rule)}
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
