#!/usr/bin/env python3
"""Q3 agreement with the additional post hoc coder (deviation D22, point 1).

Reports, in this order:
 1. the preregistered statistic first: Cohen's kappa author vs local model (D21), unchanged;
 2. Cohen's kappa Codex vs author and Codex vs local model, on packets where both labels are valid;
 3. Fleiss' kappa over the three coders, on packets where all three labels are valid;
 4. confusion tables for the three pairs;
 5. for the earlier author/local-model disagreements (disagreements-resolved.csv): how many Codex labels like the author, how many
    like the local model, how many differently.
Codex rows count only when its session was valid (column `valid` = true); excluded packets are listed.

  python3 agreement_q3_three.py --key key.csv --author coding-sheet.csv --local llm/llm-coding.csv --codex codex-coding.csv \
      --disagreements disagreements-resolved.csv --out agreement-three.json
Excluded = missing from the coder's file, or present with an invalid label or an invalid session.
"""
import argparse, collections, csv, importlib.util, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("agreement_q3", HERE / "agreement_q3.py")
agr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agr)
VALID_LABELS = {"refused-or-disconnected", "re-approval-required", "notified", "silent-accept", "stale", "error-no-call-timeout"}


def labels(path, universe, require_valid=False):
    """Labels of one coder over the expected packet ids. Duplicate ids are refused; ids of the universe with no row are
    reported as missing, rows outside the universe as unexpected."""
    rows = list(csv.DictReader(open(path, newline="")))
    if require_valid and rows and "valid" not in rows[0]:
        raise SystemExit(f"{path}: no 'valid' column")
    ids = [r["id"] for r in rows]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise SystemExit(f"{path}: duplicate packet ids {dup}")
    unexpected = sorted(set(ids) - set(universe))
    if unexpected:
        raise SystemExit(f"{path}: ids not in the packet set {unexpected}")
    out, excluded = {}, sorted(set(universe) - set(ids))
    for r in rows:
        ok = (r.get("outcome") or "").strip() in VALID_LABELS
        if require_valid:
            ok = ok and (r.get("valid") or "").strip().lower() == "true"
        if ok:
            out[r["id"]] = r["outcome"].strip()
        else:
            excluded.append(r["id"])
    return out, sorted(excluded)


def fleiss(table):
    """Fleiss' kappa. table: list of dicts label->count, every row with the same number of raters (>= 2)."""
    if not table:
        return None
    n = sum(table[0].values())
    cats = sorted({c for row in table for c in row})
    N = len(table)
    p_j = {c: sum(row.get(c, 0) for row in table) / (N * n) for c in cats}
    P_i = [(sum(v * v for v in row.values()) - n) / (n * (n - 1)) for row in table]
    P_bar = sum(P_i) / N
    P_e = sum(p * p for p in p_j.values())
    return None if P_e == 1 else (P_bar - P_e) / (1 - P_e)


def confusion(x, y):
    ids = sorted(set(x) & set(y))
    t = collections.defaultdict(lambda: collections.Counter())
    for i in ids:
        t[x[i]][y[i]] += 1
    return {a: dict(b) for a, b in sorted(t.items())}


def analyse(author, local, codex, disagreements, preregistered):
    """preregistered: agreement_q3.pair() on the raw author and local-model files, i.e. the published D21 computation."""
    res = {"1_preregistered_author_vs_local": {k: preregistered[k] for k in ("n", "agreement", "kappa")}}
    for name, other in (("2a_codex_vs_author", author), ("2b_codex_vs_local", local)):
        ids = sorted(set(codex) & set(other))
        res[name] = {"kappa": agr.kappa([codex[i] for i in ids], [other[i] for i in ids]) if ids else None, "n": len(ids),
                     "raw_agreement": (sum(codex[i] == other[i] for i in ids) / len(ids)) if ids else None}
    ids3 = sorted(set(author) & set(local) & set(codex))
    res["3_fleiss_three_coders"] = {"kappa": fleiss([collections.Counter([author[i], local[i], codex[i]]) for i in ids3]),
                                    "n": len(ids3)}
    res["4_confusion"] = {"author_x_local": confusion(author, local), "author_x_codex": confusion(author, codex),
                          "local_x_codex": confusion(local, codex)}
    like = collections.Counter()
    for d in disagreements:
        c = codex.get(d["id"])
        like["no valid codex label" if c is None else "like author" if c == d["author"] else
             "like local model" if c == d["local_model"] else "different from both"] += 1
    res["5_earlier_disagreements"] = {"n": len(disagreements), **dict(like)}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--author", required=True)
    ap.add_argument("--local", required=True)
    ap.add_argument("--codex", required=True)
    ap.add_argument("--disagreements", required=True)
    ap.add_argument("--key", required=True, help="key.csv of the blind packets: the 60 expected ids")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    universe = [r["id"] for r in csv.DictReader(open(a.key, newline=""))]
    author, ex_a = labels(a.author, universe)
    local, ex_l = labels(a.local, universe)
    codex, ex_c = labels(a.codex, universe, require_valid=True)
    dis = list(csv.DictReader(open(a.disagreements, newline="")))
    res = analyse(author, local, codex, dis, agr.pair(agr.read(a.author), agr.read(a.local)))
    res["excluded"] = {"author": ex_a, "local_model": ex_l, "codex": ex_c}
    Path(a.out).write_text(json.dumps(res, indent=2))
    print(json.dumps({k: v for k, v in res.items() if k not in ("4_confusion", "excluded")}, indent=1))


if __name__ == "__main__":
    main()
