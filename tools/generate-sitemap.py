#!/usr/bin/env python3
"""Regenerate sitemap.xml from the git-tracked pages.

    python3 tools/generate-sitemap.py

lastmod comes from the last commit that touched each page, so it can never
drift out of sync by hand. priority/changefreq follow the path depth.
"""

import subprocess
import xml.sax.saxutils as sx
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://paolocostanzo.github.io"

# pages kept out of the index: 404 and search-engine verification files
SKIP = {"404.html", "google33d681ac15aa231f.html"}


def tracked_pages():
    out = subprocess.check_output(["git", "ls-files", "*.html"], cwd=ROOT, text=True)
    for line in out.split("\n"):
        if line and line not in SKIP and Path(line).name == "index.html":
            yield line


def last_commit_date(path):
    return subprocess.check_output(
        ["git", "log", "-1", "--format=%ad", "--date=short", "--", path],
        cwd=ROOT, text=True,
    ).strip()


def url_for(path):
    if path == "index.html":
        return BASE + "/"
    return f"{BASE}/{path[: -len('index.html')]}"


def weight(path):
    if path == "index.html":
        return "1.0", "weekly"
    if path.startswith("ctf/"):
        # /ctf/ hub and series hubs: 0.8 — individual rooms: 0.7
        return ("0.7", "monthly") if path.count("/") >= 3 else ("0.8", "weekly")
    return "0.6", "monthly"


def main():
    pages = sorted(set(tracked_pages()), key=lambda p: (p != "index.html", p))
    rows = []
    for p in pages:
        prio, freq = weight(p)
        rows.append(
            f"  <url><loc>{sx.escape(url_for(p))}</loc>"
            f"<lastmod>{last_commit_date(p)}</lastmod>"
            f"<changefreq>{freq}</changefreq><priority>{prio}</priority></url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(rows)
        + "\n</urlset>\n"
    )
    (ROOT / "sitemap.xml").write_text(xml, encoding="utf-8")
    print(f"sitemap.xml regenerated — {len(rows)} URLs")


if __name__ == "__main__":
    main()
