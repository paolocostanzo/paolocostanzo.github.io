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
        "subtitle": "Research reports · reproducible labs · technical field notes",
        "footer": "paolocostanzo.github.io",
        "badge": "RESEARCH",
    },
    "ctf": {
        "out": "ctf/og.png",
        "kicker": "CTF WRITEUPS · TRYHACKME",
        "title": "CTF & WALKTHROUGHS",
        "accent": 1,
        "subtitle": "Step-by-step methodology · exploitation · defensive notes",
        "footer": "paolocostanzo.github.io/ctf",
        "badge": "INDEX",
    },
    "mcp": {
        "out": "mcp-2026-07-28-stateless-security/og.png",
        "kicker": "AGENTIC AI · MCP · TOOL AUTHORIZATION",
        "title": "STATELESS DOES NOT MEAN STATE-FREE",
        "accent": 2,
        "subtitle": "MCP 2026-07-28 · executable conformance suite · 13/13",
        "footer": "Reproducible lab",
        "badge": "HARD",
    },
    "oura": {
        "out": "oura-palantir-biometrici/og.png",
        "kicker": "PRIVACY · WEARABLE · OSINT",
        "title": "WEARABLE BIOMETRIC TELEMETRY",
        "accent": 1,
        "subtitle": "Technical tear-down and the data supply chain",
        "footer": "Original research · v2",
        "badge": "MEDIUM",
    },
    "rape-academy": {
        "out": "rape-academy-cnn-threat-intel/og.png",
        "kicker": "THREAT INTELLIGENCE · OSINT",
        "title": "COORDINATED ABUSE NETWORK ON TELEGRAM",
        "accent": 1,
        "subtitle": "Content moderation, payment rails and regulatory response",
        "footer": "Original research",
        "badge": "HARD",
    },
    "crypto-drainer": {
        "out": "crypto-drainer-svuotatasche/og.png",
        "kicker": "THREAT INTELLIGENCE · ON-CHAIN",
        "title": "TRON WALLET DRAINER-AS-A-SERVICE",
        "accent": 1,
        "subtitle": "TRC-20 approval chain · 13,960 USD traced on-chain",
        "footer": "Preprint on ResearchGate",
        "badge": "HARD",
    },
    "ssrf": {
        "out": "ssrf-imds-ec2-credentials/og.png",
        "kicker": "CLOUD · AWS · APPSEC",
        "title": "SSRF → IMDSv1: IAM CREDENTIALS FROM EC2",
        "accent": 1,
        "subtitle": "From an app-level SSRF to the role’s temporary credentials",
        "footer": "Reproducible lab",
        "badge": "HARD",
    },
    "cardputer": {
        "out": "cardputer-adv-wifi-security/og.png",
        "kicker": "NETWORK · WI-FI · HARDWARE",
        "title": "EVIL PORTAL, BEACON SPAM AND DEAUTH",
        "accent": 1,
        "subtitle": "A 40 € hardware Wi-Fi lab · 802.11 · PMF",
        "footer": "Reproducible lab",
        "badge": "MEDIUM",
    },
    "epic-fury": {
        "out": "operation-epic-fury-cyber-war-iran/og.png",
        "kicker": "THREAT INTELLIGENCE · OSINT",
        "title": "OPERATION EPIC FURY",
        "accent": 1,
        "subtitle": "Independent OSINT analysis of a dual-platform campaign",
        "footer": "Preprint on ResearchGate",
        "badge": "HARD",
    },
    "tim": {
        "out": "tim-packet-loss-gfn/og.png",
        "kicker": "NETWORK · DIAGNOSTICA",
        "title": "TIM, GEFORCE NOW AND THE ICMP BLACK HOLE",
        "accent": 2,
        "subtitle": "Packet loss, PMTUD and Wireshark analysis",
        "footer": "Field note",
        "badge": "MEDIUM",
    },
    "aws-iam": {
        "out": "aws-iam-misconfiguration/og.png",
        "kicker": "CLOUD · AWS · IAM",
        "title": "FIVE RECURRING IAM MISCONFIGURATIONS",
        "accent": 1,
        "subtitle": "How to spot them, why they matter, how to fix them",
        "footer": "Field note",
        "badge": "MEDIUM",
    },
    "prompt-injection": {
        "out": "prompt-injection-llm/og.png",
        "kicker": "AI SECURITY · LLM · APPSEC",
        "title": "PROMPT INJECTION ON ENTERPRISE LLMs",
        "accent": 1,
        "subtitle": "Input → context → tool call, and the countermeasures that hold",
        "footer": "Reproducible lab",
        "badge": "HARD",
    },
    "hh-hub": {
        "out": "ctf/hacker-holidays-2026/og.png",
        "kicker": "CTF WRITEUPS · TRYHACKME · DAY 0 TO DAY 14",
        "title": "HACKER HOLIDAYS 2026",
        "accent": 1,
        "subtitle": "Byte Lotus Hotel · one room a day · OSINT / web / cloud / forensics",
        "footer": "All 15 room writeups",
        "badge": "SERIES",
    },
    "hh-management-wants-a-word": {
        "out": "ctf/hacker-holidays-2026/management-wants-a-word/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 14 · FORENSICS",
        "title": "MANAGEMENT WANTS A WORD",
        "accent": 1,
        "subtitle": "An autologon secret unwinds DPAPI into a VeraCrypt vault",
        "footer": "CTF writeup · Day 14",
        "badge": "MEDIUM",
    },
    "hh-the-brochure": {
        "out": "ctf/hacker-holidays-2026/the-brochure/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 0 · OSINT",
        "title": "THE BROCHURE",
        "accent": 1,
        "subtitle": "From the resort brochure to an Instagram pivot on followings",
        "footer": "CTF writeup · Day 0",
        "badge": "EASY",
    },
    "hh-the-concierge-knows-too-much": {
        "out": "ctf/hacker-holidays-2026/the-concierge-knows-too-much/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 1 · AI PROMPT ATTACKS",
        "title": "THE CONCIERGE KNOWS TOO MUCH",
        "accent": 1,
        "subtitle": "VERA recognises you, she never authenticates you",
        "footer": "CTF writeup · Day 1",
        "badge": "EASY",
    },
    "hh-room-404": {
        "out": "ctf/hacker-holidays-2026/room-404/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 2 · WEB",
        "title": "ROOM 404",
        "accent": 1,
        "subtitle": "Exposed .git in production, full repo rebuilt with git-dumper",
        "footer": "CTF writeup · Day 2",
        "badge": "EASY",
    },
    "hh-complimentary": {
        "out": "ctf/hacker-holidays-2026/complimentary/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 3 · CLOUD",
        "title": "COMPLIMENTARY",
        "accent": 1,
        "subtitle": "Anonymous Cognito identity pool to dynamodb:Scan on AWS",
        "footer": "CTF writeup · Day 3",
        "badge": "MEDIUM",
    },
    "hh-packed-light": {
        "out": "ctf/hacker-holidays-2026/packed-light/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 4 · FORENSICS",
        "title": "PACKED LIGHT",
        "accent": 1,
        "subtitle": "Keystroke exfiltration hidden in HTTP cookie headers",
        "footer": "CTF writeup · Day 4",
        "badge": "MEDIUM",
    },
    "hh-beach-bar": {
        "out": "ctf/hacker-holidays-2026/beach-bar/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 5 · BOOT2ROOT",
        "title": "BEACH BAR",
        "accent": 1,
        "subtitle": "PyYAML unsafe load to RCE, then a straight path to root",
        "footer": "CTF writeup · Day 5",
        "badge": "EASY",
    },
    "hh-overheard-at-breakfast": {
        "out": "ctf/hacker-holidays-2026/overheard-at-breakfast/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 6 · OSINT",
        "title": "OVERHEARD AT BREAKFAST",
        "accent": 1,
        "subtitle": "The email that follows you around via Gravatar",
        "footer": "CTF writeup · Day 6",
        "badge": "EASY",
    },
    "hh-do-not-disturb": {
        "out": "ctf/hacker-holidays-2026/do-not-disturb/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 7 · BOOT2ROOT",
        "title": "DO NOT DISTURB",
        "accent": 1,
        "subtitle": "NoSQL injection, EJS template injection, Node inspector to root",
        "footer": "CTF writeup · Day 7",
        "badge": "MEDIUM",
    },
    "hh-towel-on-the-sunbed": {
        "out": "ctf/hacker-holidays-2026/towel-on-the-sunbed/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 8 · WEB",
        "title": "TOWEL ON THE SUNBED",
        "accent": 1,
        "subtitle": "A TOCTOU race condition between check and claim",
        "footer": "CTF writeup · Day 8",
        "badge": "MEDIUM",
    },
    "hh-cryptocabana": {
        "out": "ctf/hacker-holidays-2026/cryptocabana/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 9 · CLOUD",
        "title": "CRYPTOCABANA",
        "accent": 1,
        "subtitle": "An Azure SAS token scoped far wider than the kiosk needed",
        "footer": "CTF writeup · Day 9",
        "badge": "MEDIUM",
    },
    "hh-the-hollow-shell": {
        "out": "ctf/hacker-holidays-2026/the-hollow-shell/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 10 · WEB",
        "title": "THE HOLLOW SHELL",
        "accent": 1,
        "subtitle": "Zip-slip arbitrary write, a poisoned Jinja template, then RCE",
        "footer": "CTF writeup · Day 10",
        "badge": "MEDIUM",
    },
    "hh-infinity-pool": {
        "out": "ctf/hacker-holidays-2026/infinity-pool/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 11 · BOOT2ROOT",
        "title": "INFINITY POOL",
        "accent": 1,
        "subtitle": "Command injection into FreePBX, then root",
        "footer": "CTF writeup · Day 11",
        "badge": "MEDIUM",
    },
    "hh-after-hours": {
        "out": "ctf/hacker-holidays-2026/after-hours/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 12 · FORENSICS",
        "title": "AFTER HOURS",
        "accent": 1,
        "subtitle": "Fileless WMI persistence living in the CIM repository",
        "footer": "CTF writeup · Day 12",
        "badge": "MEDIUM",
    },
    "hh-the-guestbook": {
        "out": "ctf/hacker-holidays-2026/the-guestbook/og.png",
        "kicker": "TRYHACKME · HACKER HOLIDAYS 2026 · DAY 13 · AI / WEB",
        "title": "THE GUESTBOOK",
        "accent": 1,
        "subtitle": "Indirect prompt injection on VERA, all the way to /bin/sh",
        "footer": "CTF writeup · Day 13",
        "badge": "MEDIUM",
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
