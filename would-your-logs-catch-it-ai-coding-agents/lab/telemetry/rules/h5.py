#!/usr/bin/env python3
"""The H5 verdict from score.py's output, on client-log rules only (D27 point 4; host rules are reported apart, D36).

  - A (client, event type) pair is measurable if at least one counted event run shows the event in its ground truth
    (score.py: events.scored >= 1); otherwise it is "not measurable", listed, and left out of the verdict.
  - An event type is distinguishable if, for every measurable pair of that type, the pair's rule tells the event apart
    (score.py: tells_apart, i.e. detects every scored run and stays below 5% of holdout windows).
  - H5 holds if at least one event type with at least one measurable pair is not distinguishable.
  - A client whose confirmatory data are incomplete (scores_input's "incomplete_clients": the runner's provider-incomplete clients
    of D28 point 6, and every client with an absent or unusable planned run or holdout) is marked preliminary: the verdict is
    given with and without its pairs. Otherwise a measurable pair with no holdout windows does not pass.
  - What is missing (scores_input's "missing") is listed; the verdict with all clients is then provisional.

  python3 h5.py SCORES.json SCORES_INPUT.json > H5.json
"""
import json, sys


def verdict(scores, incomplete=(), missing=()):
    if any("/" not in rid or rid.startswith("host/") for rid in scores):
        sys.exit("h5.py takes client-log rules only; host rules are reported apart (D36)")
    pairs = {}
    for rid, s in sorted(scores.items()):
        client, event = rid.split("/", 1)
        measurable = s["events"]["scored"] >= 1
        ok = bool(s["tells_apart"]) and s["holdout"]["windows"] > 0
        pairs[rid] = {"client": client, "event": event, "measurable": measurable, "tells_apart": ok if measurable else None,
                      "preliminary": client in incomplete,
                      "detected": f"{s['events']['detected']}/{s['events']['scored']}",
                      "fp_windows": f"{s['holdout']['fp_windows']}/{s['holdout']['windows']}"}

    def judge(keep):
        types = {}
        for p in pairs.values():
            if p["measurable"] and keep(p):
                types.setdefault(p["event"], []).append(p["tells_apart"])
        by_type = {e: {"measurable_pairs": len(v), "distinguishable": all(v)} for e, v in sorted(types.items())}
        return {"h5_holds": any(not t["distinguishable"] for t in by_type.values()), "event_types": by_type}

    out = {"all_clients": judge(lambda p: True), "pairs": pairs,
           "not_measurable": sorted(r for r, p in pairs.items() if not p["measurable"]),
           "incomplete_clients": sorted(incomplete), "missing": list(missing), "provisional": bool(missing)}
    if incomplete:
        out["without_incomplete_clients"] = judge(lambda p: not p["preliminary"])
    out["h5_holds"], out["event_types"] = out["all_clients"]["h5_holds"], out["all_clients"]["event_types"]
    return out


if __name__ == "__main__":
    inp = json.load(open(sys.argv[2]))
    print(json.dumps(verdict(json.load(open(sys.argv[1])), inp.get("incomplete_clients", []), inp.get("missing", [])), indent=1))
