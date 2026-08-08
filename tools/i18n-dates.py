#!/usr/bin/env python3
"""
Make the article dates bilingual.

The CTF pages already carried their dates as data-it/data-en pairs, but the older
articles and the homepage cards hard-coded the Italian form, so English readers
were served "24 Mag 2026". This rewrites those elements into the same bilingual
shape the rest of the site uses: English rendered, Italian kept in data-it.
"""
import glob
import html
import os
import re
import sys
from html.parser import HTMLParser

VOID = {'meta', 'link', 'br', 'img', 'hr', 'input', 'source', 'area',
        'base', 'col', 'embed', 'param', 'track', 'wbr'}

MONTHS = {'Gen': 'Jan', 'Feb': 'Feb', 'Mar': 'Mar', 'Apr': 'Apr',
          'Mag': 'May', 'Giu': 'Jun', 'Lug': 'Jul', 'Ago': 'Aug',
          'Set': 'Sep', 'Ott': 'Oct', 'Nov': 'Nov', 'Dic': 'Dec'}

DATE = re.compile(r'^\s*(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(\d{4})\s*$')


class Spans(HTMLParser):
    def __init__(self, raw):
        super().__init__(convert_charrefs=False)
        self.raw = raw
        self.starts = [0]
        for i, ch in enumerate(raw):
            if ch == '\n':
                self.starts.append(i + 1)
        self.stack = []
        self.hits = []

    def _abs(self):
        line, off = self.getpos()
        return self.starts[line - 1] + off

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        text = self.get_starttag_text()
        pos = self._abs()
        self.stack.append((tag, pos, pos + len(text), dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        return

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                _, tag_start, inner_start, at = self.stack[i]
                if 'data-en' not in at and 'data-it' not in at:
                    inner = self.raw[inner_start:self._abs()]
                    if DATE.match(html.unescape(inner)):
                        self.hits.append((tag_start, inner_start, self._abs(), inner))
                del self.stack[i:]
                return


def translate(it_date):
    m = DATE.match(it_date)
    day, mon, year = m.group(1), m.group(2), m.group(3)
    return f'{day} {MONTHS[mon]} {year}'


def process(raw):
    p = Spans(raw)
    p.feed(raw)
    p.close()
    out = raw
    n = 0
    for tag_start, inner_start, inner_end, inner in sorted(p.hits, key=lambda h: -h[0]):
        it = inner.strip()
        en = translate(it)
        if en == it:
            continue
        head = out[tag_start:inner_start]
        close = head.rfind('>')
        # keep the trailing slash of a self-closing form out of the way
        new_head = head[:close] + f' data-it="{it}" data-en="{en}"' + head[close:]
        out = out[:tag_start] + new_head + en + out[inner_end:]
        n += 1
    return out, n


def main():
    root = sys.argv[1]
    write = '--write' in sys.argv
    files = sorted(set(glob.glob(os.path.join(root, '*/index.html')) +
                       glob.glob(os.path.join(root, 'ctf/**/index.html'), recursive=True) +
                       [os.path.join(root, 'index.html'), os.path.join(root, '404.html')]))
    total = 0
    for f in files:
        if not os.path.exists(f):
            continue
        raw = open(f, encoding='utf-8').read()
        out, n = process(raw)
        if n:
            total += n
            print(f'{n:>3}  {os.path.relpath(f, root)}')
            if write:
                open(f, 'w', encoding='utf-8').write(out)
    print(f'\n{total} dates made bilingual' + ('' if write else '  (DRY RUN — pass --write)'))


if __name__ == '__main__':
    main()
