#!/usr/bin/env python3
"""Generate assets/htb-card-dark.svg and assets/htb-card-light.svg from the
public Hack The Box profile (rank, level/grade with XP progress, global rank).

HTB navy/green with the HTB mark (Simple Icons, CC0; never recoloured), Share
Tech Mono + Barlow Condensed embedded so it renders the same inside GitHub's
<img> sandbox. Run at build time by .github/workflows/deploy-pages.yml (daily via htb-card.yml); tools/ is stripped from the Pages artifact. On any API
problem it exits without touching the existing cards.
"""
import base64, datetime, html, json, pathlib, sys, urllib.request

LOGO = "m22.5106 6.4566.0008-.0123a.888.888 0 0 0-.2717-.6384c-.0084-.0084-.018-.0155-.0267-.0235-.0186-.0166-.0371-.0333-.0572-.0484-.0193-.0147-.04-.0276-.0607-.0406-.0096-.006-.0182-.0131-.0281-.0188L12.4576.1266a.891.891 0 0 0-.9223.0043L1.933 5.6744c-.0107.0062-.0203.014-.0307.0205-.0073.0047-.015.008-.0223.0128-.007.0047-.013.0106-.02.0155a.8769.8769 0 0 0-.147.1333l-.0026.003a.8872.8872 0 0 0-.2218.5847l.0009.014c-.0002.0088-.0015.0176-.0015.0264v11.0708c0 .3277.1802.6288.469.7836l9.5986 5.5417c.0076.0044.0158.0075.0236.0117a.8754.8754 0 0 0 .166.0687c.0134.004.0266.0083.0401.0117a.8793.8793 0 0 0 .072.0142c.0117.0019.0232.0045.0349.006a.835.835 0 0 0 .2157 0c.0117-.0015.0232-.0041.0348-.006a.9.9 0 0 0 .072-.0142c.0135-.0034.0267-.0077.04-.0117a.895.895 0 0 0 .0646-.0217.9134.9134 0 0 0 .1015-.047c.0078-.0042.016-.0072.0236-.0117l9.5986-5.5417a.8888.8888 0 0 0 .469-.7836V6.4779c0-.0071-.0012-.0142-.0014-.0213zM5.2543 6.0822l6.5367-3.774a.4182.4182 0 0 1 .4182 0l6.5366 3.774a.4182.4182 0 0 1 0 .7243l-6.5367 3.774a.4182.4182 0 0 1-.4182 0l-6.5366-3.774a.4182.4182 0 0 1 0-.7243zm5.6134 14.3449a.4172.4172 0 0 1-.626.3613L3.718 17.0218a.4173.4173 0 0 1-.2086-.3613V9.1279a.4172.4172 0 0 1 .6258-.3613l6.524 3.7666a.4172.4172 0 0 1 .2086.3614v7.5325zm9.623-3.7666a.4173.4173 0 0 1-.2086.3613l-6.5239 3.7666a.4172.4172 0 0 1-.6259-.3613v-7.5325c0-.149.0796-.2868.2087-.3614l6.5239-3.7666a.4172.4172 0 0 1 .6258.3613v7.5326z"  # Simple Icons (CC0), Hack The Box mark
USER_ID = 2960827
SHOW_GLOBAL_UNDER = 1000  # hide the volatile global position if it drifts past this
ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
STATE = ASSETS / "htb-state.json"
BASE = "https://labs.hackthebox.com/api"
UA = "paolocostanzo-readme-card (+https://github.com/paolocostanzo)"
ROMAN = {"1": "I", "2": "II", "3": "III"}

THEMES = {
    "dark": dict(bg="#141D2B", border="#24344A", text="#E8EDF3", muted="#8A97A8",
                 accent="#9FEF00", track="#24344A"),
    "light": dict(bg="#FFFFFF", border="#D0D7DE", text="#141D2B", muted="#5B6B80",
                  accent="#141D2B", track="#E4E9EF"),
}


def get(path):
    req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def font(name):
    return base64.b64encode((ASSETS / "fonts" / f"{name}.woff2").read_bytes()).decode()


def fetch():
    p = get(f"v4/profile/{USER_ID}")["profile"]
    x = get(f"experience/v1/account/{p['account_id']}")
    data = dict(rank=p["rank"], rank_id=int(p["rank_id"]), ranking=p.get("ranking"),
                level=int(x["level"]), title=x["levelTitle"], grade=str(x["levelGrade"]),
                lvl_xp=int(x["levelExperiencePoints"]), to_next=int(x["experienceUntilNextLevel"]))
    if not data["rank"] or data["level"] < 1:
        raise ValueError("incomplete profile data")
    # HTB rank tiers are kept when content retires: never show a lower tier than one already earned.
    prev = json.loads(STATE.read_text()) if STATE.exists() else {}
    if prev.get("rank_id", 0) > data["rank_id"]:
        data["rank"], data["rank_id"] = prev["rank"], prev["rank_id"]
    return data


def render(d, t, mono, cond):
    rank = html.escape(d["rank"].upper())
    level = f"LEVEL {d['level']} · {html.escape(d['title'].upper())} {ROMAN.get(d['grade'], d['grade'])}"
    span = d["lvl_xp"] + d["to_next"]
    pct = max(0.0, min(1.0, d["lvl_xp"] / span)) if span else 0.0
    bar_w = 170
    show_global = d["ranking"] and int(d["ranking"]) <= SHOW_GLOBAL_UNDER
    month = datetime.date.today().strftime("%b %Y").upper()
    label = f"Hack The Box: {d['rank']}, level {d['level']} {d['title']} {ROMAN.get(d['grade'], d['grade'])}"
    if show_global:
        label += f", global rank #{d['ranking']}"
    right = ""
    if show_global:
        right = f'''<line x1="252" y1="14" x2="252" y2="62" stroke="{t['border']}"/>
<text x="346" y="25" class="m" font-size="10" fill="{t['muted']}" text-anchor="end" letter-spacing="1">GLOBAL</text>
<text x="346" y="49" class="c" font-size="24" fill="{t['accent']}" text-anchor="end">#{d['ranking']}</text>
<text x="346" y="62" class="m" font-size="8.5" fill="{t['muted']}" text-anchor="end" letter-spacing="0.5">{month}</text>'''
    return f'''<svg width="360" height="76" viewBox="0 0 360 76" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{label}">
<title>{label}</title>
<style>
@font-face{{font-family:'STM';src:url(data:font/woff2;base64,{mono}) format('woff2');}}
@font-face{{font-family:'BC';src:url(data:font/woff2;base64,{cond}) format('woff2');}}
.m{{font-family:'STM','Share Tech Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}}
.c{{font-family:'BC','Barlow Condensed','Arial Narrow',sans-serif;font-weight:900;}}
</style>
<rect x="0.5" y="0.5" width="359" height="75" rx="9" fill="{t['bg']}" stroke="{t['border']}"/>
<g transform="translate(14 25)"><path fill="#9FEF00" d="{LOGO}"/></g>
<text x="50" y="23" class="m" font-size="10" fill="{t['muted']}" letter-spacing="1">HACK THE BOX</text>
<text x="49" y="44" class="c" font-size="21" fill="{t['text']}" letter-spacing="0.4">{rank}</text>
<text x="50" y="58" class="m" font-size="9.5" fill="{t['muted']}" letter-spacing="0.5">{level}</text>
<rect x="50" y="63" width="{bar_w}" height="3" rx="1.5" fill="{t['track']}"/>
<rect x="50" y="63" width="{bar_w * pct:.1f}" height="3" rx="1.5" fill="#9FEF00"/>
{right}
</svg>
'''


def main():
    try:
        d = fetch()
    except Exception as e:  # keep the last good cards
        sys.exit(f"HTB fetch failed, cards left unchanged: {e}")
    mono, cond = font("share-tech-mono-latin-400-normal"), font("barlow-condensed-latin-900-normal")
    for name, t in THEMES.items():
        (ASSETS / f"htb-card-{name}.svg").write_text(render(d, t, mono, cond))
    STATE.write_text(json.dumps({"rank": d["rank"], "rank_id": d["rank_id"]}) + "\n")
    print(f"{d['rank']} · level {d['level']} {d['title']} {d['grade']} · #{d['ranking']}")


if __name__ == "__main__":
    main()
