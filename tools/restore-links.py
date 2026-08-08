#!/usr/bin/env python3
"""
Put back the outbound links the language flip dropped.

tools/i18n-flip.py replaced each element's content with its data-en value. Where
the Italian DOM carried an inline <a> but the data-en string was plain text, the
link went with it — eleven of them, including the two GitHub evidence links on the
crypto-drainer investigation and six citation markers on the Oura tear-down.

Both the rendered content AND the data-en attribute are repaired. Fixing only the
rendered text would look right until someone used the toggle: setLang() re-applies
data-en as innerHTML, so a link missing from the attribute is lost again on the
next switch back to English.

Anchor text is a proper noun or a citation marker that reads the same in both
languages (PhishDestroy, ScamIntelLogs, ringverse/protocol, [1]..[6]). Where the
English text still contains it the link is re-wrapped around it; where the English
dropped a citation marker entirely the marker is re-attached at the end. Anything
that cannot be placed exactly is reported rather than guessed at.
"""
import html
import re
import subprocess
import sys
from html.parser import HTMLParser

from bs4 import BeautifulSoup

BEFORE = '5debda4'  # last commit before the English flip
VOID = {'meta', 'link', 'br', 'img', 'hr', 'input', 'source', 'area',
        'base', 'col', 'embed', 'param', 'track', 'wbr'}


class Spans(HTMLParser):
    """For every [data-en] element: the span of its start tag and of its content."""

    def __init__(self, raw):
        super().__init__(convert_charrefs=False)
        self.starts = [0]
        for i, ch in enumerate(raw):
            if ch == '\n':
                self.starts.append(i + 1)
        self.stack = []
        self.found = []

    def _abs(self):
        line, off = self.getpos()
        return self.starts[line - 1] + off

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        text = self.get_starttag_text()
        pos = self._abs()
        self.stack.append((tag, pos, pos + len(text), dict(attrs).get('data-en')))

    def handle_startendtag(self, tag, attrs):
        return

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                _, tag_start, inner, den = self.stack[i]
                if den is not None:
                    self.found.append(dict(tag_start=tag_start, inner=inner,
                                           end=self._abs(), data_en=den))
                del self.stack[i:]
                return


def esc_attr(value):
    return value.replace('&', '&amp;').replace('"', '&quot;')


def lost_links(path):
    """Elements whose data-en dropped an <a> the Italian DOM had, grouped per element."""
    old = subprocess.run(['git', 'show', f'{BEFORE}:{path}'],
                         capture_output=True, text=True).stdout
    soup = BeautifulSoup(old, 'html.parser')
    per_element = {}
    for el in soup.select('[data-en]'):
        if any(p.has_attr('data-en') for p in el.parents):
            continue
        anchors = el.find_all('a')
        if not anchors or '<a ' in el['data-en']:
            continue
        per_element.setdefault(el['data-en'], []).extend(
            dict(tag=str(a), text=a.get_text(' ').strip(), href=a.get('href', ''))
            for a in anchors)
    return per_element


def rebuild(body, anchors, failed):
    """Apply every anchor belonging to one element to that element's English text."""
    for item in anchors:
        text, tag = item['text'], item['tag']
        if item['href'] in body:
            continue
        anchor = re.sub(r'>.*</a>$', '>' + text + '</a>', tag, flags=re.S)
        if text and text in body:
            if body.count(text) > 1:
                failed.append((item['href'], f'anchor text {text!r} ambiguous'))
                continue
            body = body.replace(text, anchor, 1)
        elif re.fullmatch(r'\[\d+\]', text or ''):
            body = body.rstrip() + ' ' + anchor      # citation marker the English dropped
        else:
            failed.append((item['href'], f'anchor text {text!r} absent from English text'))
    return body


def repair(path, write):
    raw = open(path, encoding='utf-8').read()
    wanted = lost_links(path)
    failed = []

    p = Spans(raw)
    p.feed(raw)
    p.close()
    targets = [f for f in p.found if f['data_en'] in wanted]

    done = 0
    for f in sorted(targets, key=lambda f: -f['tag_start']):
        anchors = wanted[f['data_en']]
        body = raw[f['inner']:f['end']]
        new_body = rebuild(body, anchors, failed)
        if new_body == body:
            continue
        start_tag = raw[f['tag_start']:f['inner']]
        new_tag = re.sub(r'data-en="(?:[^"\\]|\\.)*"',
                         lambda m: 'data-en="%s"' % esc_attr(html.unescape(new_body).strip()),
                         start_tag, count=1)
        raw = raw[:f['tag_start']] + new_tag + new_body + raw[f['end']:]
        done += 1

    if write and done:
        open(path, 'w', encoding='utf-8').write(raw)
    return done, failed


def main():
    write = '--write' in sys.argv
    for p in [a for a in sys.argv[1:] if not a.startswith('--')]:
        done, failed = repair(p, write)
        print(f'{done:>3} elements repaired   {p}')
        for href, why in failed:
            print(f'      NOT restored: {href[:66]}  ({why})')
    print('\n' + ('written' if write else 'DRY RUN — pass --write'))


if __name__ == '__main__':
    main()
