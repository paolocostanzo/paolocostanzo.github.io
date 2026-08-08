#!/usr/bin/env python3
"""Generate the site's Open Graph images (1200x630) in the CTF section's style.

Usage:
    python3 tools/generate-og.py            # regenerate every image in the manifest
    python3 tools/generate-og.py ctf home   # regenerate only the given keys

Images are written next to the page they represent (e.g. ctf/og.png).
Fonts: Arial Bold for the title, Menlo for mono. On Linux the DejaVu fonts are
required instead — see FONT_CANDIDATES.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent

W, H = 1200, 630
BG = "#0a0a0a"
GRID = "#161616"
ORANGE = "#ff6a00"
WHITE = "#e8e8e8"
GREY = "#8a8a8a"

MARGIN = 100
BAR_W = 10

FONT_CANDIDATES = {
    "bold": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ],
    "mono": [
        "/System/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ],
}


def font(kind, size):
    for path in FONT_CANDIDATES[kind]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit(f"no '{kind}' font found: install DejaVu or Liberation")


def text_w(draw, s, f, tracking=0):
    if not s:
        return 0
    w = draw.textlength(s, font=f)
    return w + tracking * (len(s) - 1)


def draw_tracked(draw, xy, s, f, fill, tracking=0):
    """Draw text with letter-spacing (PIL has no native support for it)."""
    x, y = xy
    if not tracking:
        draw.text((x, y), s, font=f, fill=fill)
        return x + draw.textlength(s, font=f)
    for ch in s:
        draw.text((x, y), ch, font=f, fill=fill)
        x += draw.textlength(ch, font=f) + tracking
    return x


def wrap(draw, words, f, max_w, tracking=0):
    lines, cur = [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if text_w(draw, trial, f, tracking) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def fit_title(draw, title, accent_words, max_w, max_lines=2, hi=86, lo=40):
    """Shrink the point size until the title fits in max_lines lines.

    Returns (font, lines) where each line is a list of (word, color).
    This is exactly where the old ctf/og.png bug lived: with no overflow check
    the title ran straight off the canvas.
    """
    words = title.split()
    n_accent = min(accent_words, len(words))
    colors = [WHITE] * (len(words) - n_accent) + [ORANGE] * n_accent

    size = hi
    while size >= lo:
        f = font("bold", size)
        lines = wrap(draw, words, f, max_w)
        if len(lines) <= max_lines:
            break
        size -= 2
    else:
        f = font("bold", lo)
        lines = wrap(draw, words, f, max_w)[:max_lines]

    out, i = [], 0
    for line in lines:
        row = []
        for word in line.split():
            row.append((word, colors[i] if i < len(colors) else WHITE))
            i += 1
        out.append(row)
    return f, out


def render(spec, out_path):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    for x in range(0, W, 40):
        d.line([(x, 0), (x, H)], fill=GRID, width=1)
    for y in range(0, H, 40):
        d.line([(0, y), (W, y)], fill=GRID, width=1)
    d.rectangle([0, 0, BAR_W - 1, H], fill=ORANGE)

    f_kicker = font("mono", 26)
    draw_tracked(d, (MARGIN, 68), f"// {spec['kicker']}", f_kicker, ORANGE, tracking=4)

    max_w = W - MARGIN * 2
    f_title, lines = fit_title(d, spec["title"], spec.get("accent", 1), max_w)
    line_h = int(f_title.size * 1.12)
    block_h = line_h * len(lines)
    y = 300 - block_h // 2
    for row in lines:
        x = MARGIN
        for word, color in row:
            d.text((x, y), word, font=f_title, fill=color)
            x += d.textlength(word + " ", font=f_title)
        y += line_h

    f_sub = font("mono", 28)
    sub_lines = wrap(d, spec["subtitle"].split(), f_sub, max_w)[:2]
    y += 18
    for line in sub_lines:
        d.text((MARGIN, y), line, font=f_sub, fill=GREY)
        y += 38

    f_foot = font("mono", 22)
    fy = H - 96
    x = draw_tracked(d, (MARGIN, fy), "nonè", f_foot, ORANGE)
    x = draw_tracked(d, (x, fy), "@costanzo", f_foot, WHITE)
    x = draw_tracked(d, (x, fy), ":~$", f_foot, ORANGE)
    draw_tracked(d, (x + 22, fy), f"·  {spec['footer']}", f_foot, GREY)

    badge = spec.get("badge")
    if badge:
        f_badge = font("mono", 20)
        bw = int(text_w(d, badge, f_badge, 3)) + 44
        bx0, bx1 = W - MARGIN - bw, W - MARGIN
        d.rectangle([bx0, fy - 12, bx1, fy + 38], outline=ORANGE, width=1)
        draw_tracked(d, (bx0 + 22, fy + 3), badge, f_badge, ORANGE, tracking=3)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    print(f"  {out_path.relative_to(ROOT)}  ({out_path.stat().st_size // 1024} KB)")


MANIFEST = {
    "home": {
        "out": "og.png",
        "kicker": "CLOUD & AI SECURITY RESEARCH",
        "title": "PAOLO COSTANZO RESEARCH",
        "accent": 1,
        "subtitle": "Research report · lab riproducibili · note tecniche",
        "footer": "paolocostanzo.github.io",
        "badge": "RESEARCH",
    },
    "ctf": {
        "out": "ctf/og.png",
        "kicker": "CTF WRITEUPS · TRYHACKME",
        "title": "CTF & WALKTHROUGHS",
        "accent": 1,
        "subtitle": "Metodologia passo-passo · exploitation · note difensive",
        "footer": "paolocostanzo.github.io/ctf",
        "badge": "INDEX",
    },
    "mcp": {
        "out": "mcp-2026-07-28-stateless-security/og.png",
        "kicker": "AGENTIC AI · MCP · TOOL AUTHORIZATION",
        "title": "STATELESS NON SIGNIFICA SENZA STATO",
        "accent": 2,
        "subtitle": "MCP 2026-07-28 · conformance test eseguibile · 13/13",
        "footer": "Lab riproducibile",
        "badge": "HARD",
    },
    "oura": {
        "out": "oura-palantir-biometrici/og.png",
        "kicker": "PRIVACY · WEARABLE · OSINT",
        "title": "TELEMETRIA BIOMETRICA INDOSSABILE",
        "accent": 1,
        "subtitle": "Tear-down tecnico e catena di trattamento dei dati",
        "footer": "Ricerca originale · v2",
        "badge": "MEDIUM",
    },
    "rape-academy": {
        "out": "rape-academy-cnn-threat-intel/og.png",
        "kicker": "THREAT INTELLIGENCE · OSINT",
        "title": "RETE DI ABUSO COORDINATA SU TELEGRAM",
        "accent": 1,
        "subtitle": "Content moderation, payment rail e risposta regolatoria",
        "footer": "Ricerca originale",
        "badge": "HARD",
    },
    "crypto-drainer": {
        "out": "crypto-drainer-svuotatasche/og.png",
        "kicker": "THREAT INTELLIGENCE · ON-CHAIN",
        "title": "TRON WALLET DRAINER-AS-A-SERVICE",
        "accent": 1,
        "subtitle": "Catena di approvazione TRC-20 · 13.960 USD tracciati on-chain",
        "footer": "Preprint su ResearchGate",
        "badge": "HARD",
    },
    "ssrf": {
        "out": "ssrf-imds-ec2-credentials/og.png",
        "kicker": "CLOUD · AWS · APPSEC",
        "title": "SSRF → IMDSv1: CREDENZIALI IAM DA EC2",
        "accent": 1,
        "subtitle": "Dalla SSRF applicativa alle credenziali temporanee del ruolo",
        "footer": "Lab riproducibile",
        "badge": "HARD",
    },
    "cardputer": {
        "out": "cardputer-adv-wifi-security/og.png",
        "kicker": "NETWORK · WI-FI · HARDWARE",
        "title": "EVIL PORTAL, BEACON SPAM E DEAUTH",
        "accent": 1,
        "subtitle": "Lab Wi-Fi su hardware da 40 € · 802.11 · PMF",
        "footer": "Lab riproducibile",
        "badge": "MEDIUM",
    },
    "epic-fury": {
        "out": "operation-epic-fury-cyber-war-iran/og.png",
        "kicker": "THREAT INTELLIGENCE · OSINT",
        "title": "OPERATION EPIC FURY",
        "accent": 1,
        "subtitle": "Analisi OSINT indipendente di una campagna dual-platform",
        "footer": "Preprint su ResearchGate",
        "badge": "HARD",
    },
    "tim": {
        "out": "tim-packet-loss-gfn/og.png",
        "kicker": "NETWORK · DIAGNOSTICA",
        "title": "TIM, GEFORCE NOW E L'ICMP BLACK HOLE",
        "accent": 2,
        "subtitle": "Packet loss, PMTUD e analisi con Wireshark",
        "footer": "Field note",
        "badge": "MEDIUM",
    },
    "aws-iam": {
        "out": "aws-iam-misconfiguration/og.png",
        "kicker": "CLOUD · AWS · IAM",
        "title": "CINQUE MISCONFIGURAZIONI IAM RICORRENTI",
        "accent": 1,
        "subtitle": "Come si individuano, perché contano e come si correggono",
        "footer": "Field note",
        "badge": "MEDIUM",
    },
    "guestbook": {
        "out": "ctf/hacker-holidays-2026/the-guestbook/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · AI / WEB",
        "title": "THE GUESTBOOK",
        "accent": 1,
        "subtitle": "Prompt injection indiretta su VERA, fino a /bin/sh",
        "footer": "CTF writeup",
        "badge": "MEDIUM",
    },
    "prompt-injection": {
        "out": "prompt-injection-llm/og.png",
        "kicker": "AI SECURITY · LLM · APPSEC",
        "title": "PROMPT INJECTION SU LLM AZIENDALI",
        "accent": 1,
        "subtitle": "Catena input → contesto → tool call e contromisure applicabili",
        "footer": "Lab riproducibile",
        "badge": "HARD",
    },
}


def main():
    keys = sys.argv[1:] or list(MANIFEST)
    unknown = [k for k in keys if k not in MANIFEST]
    if unknown:
        raise SystemExit(f"unknown keys: {', '.join(unknown)}")
    print(f"Generating {len(keys)} OG image(s):")
    for key in keys:
        spec = MANIFEST[key]
        render(spec, ROOT / spec["out"])


if __name__ == "__main__":
    main()
