#!/usr/bin/env python3
"""
Keep feed.xml in step with the CTF writeups.

The feed only ever carried the series hub, so the 14 individual rooms were
invisible to anything reading it — including the GitHub profile README, which
pulls this feed once a day. Each room becomes its own <item>, ordered newest
first, with the title, description and publication date taken from the page
itself so the feed cannot drift from what is published.
"""
import glob
import html
import os
import re
import sys
from datetime import datetime

BASE = 'ctf/hacker-holidays-2026'
MONTHS = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
          'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}


def page_meta(path):
    raw = open(path, encoding='utf-8').read()
    title = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    desc = re.search(r'<meta name="description"[^>]*content="((?:[^"\\]|\\.)*)"', raw)
    date = re.search(r'(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(\d{4})', raw)
    cat = re.search(r'class="c-cat"[^>]*>([^<]+)<', raw) or \
          re.search(r'class="[^"]*\bcat\b[^"]*"[^>]*>([^<]+)<', raw)
    if not (title and desc and date):
        return None
    dt = datetime(int(date.group(3)), MONTHS[date.group(2)], int(date.group(1)))
    return dict(
        title=html.unescape(title.group(1)).strip(),
        desc=html.unescape(desc.group(1)).strip(),
        dt=dt,
        cat=html.unescape(cat.group(1)).strip() if cat else 'CTF',
    )


def item_xml(url, m):
    cats = ['CTF', 'Walkthrough', 'TryHackMe']
    if m['cat'] and m['cat'] not in cats:
        cats.append(m['cat'])
    cat_xml = '\n      '.join(f'<category>{html.escape(c)}</category>' for c in cats)
    return (
        '    <item>\n'
        f'      <title>{html.escape(m["title"])}</title>\n'
        f'      <link>{url}</link>\n'
        f'      <guid>{url}</guid>\n'
        f'      <pubDate>{m["dt"].strftime("%a, %d %b %Y")} 00:00:00 +0000</pubDate>\n'
        f'      <description>{html.escape(m["desc"])}</description>\n'
        f'      {cat_xml}\n'
        '    </item>'
    )


def main():
    root = sys.argv[1]
    write = '--write' in sys.argv
    feed_path = os.path.join(root, 'feed.xml')
    feed = open(feed_path, encoding='utf-8').read()

    rooms = []
    for path in sorted(glob.glob(os.path.join(root, BASE, '*/index.html'))):
        slug = os.path.basename(os.path.dirname(path))
        url = f'https://paolocostanzo.github.io/{BASE}/{slug}/'
        if f'<link>{url}</link>' in feed:
            print(f'  already present  {slug}')
            continue
        m = page_meta(path)
        if not m:
            print(f'  NO METADATA      {slug}')
            continue
        rooms.append((url, m, slug))

    rooms.sort(key=lambda r: r[1]['dt'], reverse=True)
    for url, m, slug in rooms:
        print(f'  + {m["dt"]:%d %b %Y}  {slug}')

    if rooms:
        block = '\n'.join(item_xml(u, m) for u, m, _ in rooms)
        # insert right after the series hub item, keeping the feed newest-first
        anchor = f'<link>https://paolocostanzo.github.io/{BASE}/</link>'
        idx = feed.index('</item>', feed.index(anchor)) + len('</item>')
        feed = feed[:idx] + '\n' + block + feed[idx:]

    n = feed.count('<item>')
    print(f'\n{len(rooms)} rooms added — feed now has {n} items'
          + ('' if write else '   (DRY RUN — pass --write)'))
    if write:
        open(feed_path, 'w', encoding='utf-8').write(feed)


if __name__ == '__main__':
    main()
