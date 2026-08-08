#!/usr/bin/env python3
"""
Flip the rendered language of paolocostanzo.github.io from Italian to English.

The site is bilingual through data-it / data-en attributes; the rendered DOM has
always been the Italian one, so search engines have only ever seen Italian. This
makes English the served language at the same URLs, leaving Italian reachable
through the in-page toggle.

Edits are surgical string splices on the raw source: BeautifulSoup round-trips
are not byte-faithful on this hand-written HTML (it rewrites hundreds of lines
per file even with no changes), so it is used only to locate, never to serialize.

Stages
  1  repair data-* attributes escaped JS-style (\") which HTML truncates
  2  render data-en into the DOM and switch every language default to English

data-it is deliberately left untouched. On the ten non-CTF articles it has drifted
from the rendered DOM in 281 places (a pre-existing inconsistency, zero occurrences
on the CTF pages); rewriting it would mean re-escaping 281 fragments by hand for no
gain on the pages this work is about, so the drift is reported rather than papered over.
"""
import glob
import html
import os
import re
import sys
from html.parser import HTMLParser

VOID = {'meta', 'link', 'br', 'img', 'hr', 'input', 'source', 'area',
        'base', 'col', 'embed', 'param', 'track', 'wbr'}

ATTR_ESCAPED = re.compile(r'data-(it|en)="((?:[^"\\]|\\.)*)"')


def pages(root):
    found = sorted(glob.glob(os.path.join(root, '*/index.html')))
    found += sorted(glob.glob(os.path.join(root, 'ctf/**/index.html'), recursive=True))
    found += [os.path.join(root, 'index.html'), os.path.join(root, '404.html')]
    return sorted(set(f for f in found if os.path.exists(f)))


class Locator(HTMLParser):
    """Records the source span of the inner content of every [data-en] element."""

    def __init__(self, raw):
        super().__init__(convert_charrefs=False)
        self.raw = raw
        self.starts = [0]
        for i, ch in enumerate(raw):
            if ch == '\n':
                self.starts.append(i + 1)
        self.stack = []
        self.spans = []
        self.unbalanced = 0

    def _abs(self):
        line, off = self.getpos()
        return self.starts[line - 1] + off

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        text = self.get_starttag_text()
        inner = self._abs() + len(text)
        self.stack.append((tag, inner, dict(attrs).get('data-en')))

    def handle_startendtag(self, tag, attrs):
        return  # self-closing: no inner content to rewrite

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                _, inner, den = self.stack[i]
                if den is not None:
                    self.spans.append((inner, self._abs(), den))
                if i != len(self.stack) - 1:
                    self.unbalanced += len(self.stack) - 1 - i
                del self.stack[i:]
                return
        self.unbalanced += 1


def locate(raw):
    p = Locator(raw)
    p.feed(raw)
    p.close()
    return p


# ── stage 1 ────────────────────────────────────────────────────────────────
def repair_escapes(raw):
    """\\" is JavaScript escaping; in HTML the attribute just ends at the quote."""
    n = 0

    def sub(m):
        nonlocal n
        val = m.group(2)
        if '\\"' not in val:
            return m.group(0)
        n += 1
        return 'data-%s="%s"' % (m.group(1), val.replace('\\"', '&quot;'))

    return ATTR_ESCAPED.sub(sub, raw), n


def count_drift(raw):
    """How many elements have data-it out of step with the published DOM."""

    class ItLocator(Locator):
        def handle_starttag(self, tag, attrs):
            if tag in VOID:
                return
            inner = self._abs() + len(self.get_starttag_text())
            self.stack.append((tag, inner, dict(attrs).get('data-it')))

    p = ItLocator(raw)
    p.feed(raw)
    p.close()
    return sum(1 for s, e, dit in p.spans
               if html.unescape(raw[s:e]).strip() != html.unescape(dit).strip())


# ── stage 2 ────────────────────────────────────────────────────────────────
def fix_resolve_initial(raw):
    """resolveInitialLang() decides the language for a visitor with no stored choice.

    Eight pages sniffed navigator.language and auto-switched to Italian; the other
    twenty never did, so the same visitor got a different language depending on which
    page they landed on. Every return inside the function becomes 'en', which leaves
    the localStorage lookup above it as the only route to Italian: English is served
    everywhere and only an explicit click on IT changes it.
    """
    out = raw
    n = 0
    m = re.search(r'function\s+resolveInitialLang\s*\(\s*\)\s*\{', out)
    if not m:
        return out, 0
    i = m.end() - 1
    depth = 0
    for j in range(i, len(out)):
        if out[j] == '{':
            depth += 1
        elif out[j] == '}':
            depth -= 1
            if depth == 0:
                break
    else:
        return out, 0
    body = out[i:j + 1]
    body, n = re.subn(r"return 'it';", "return 'en';", body)
    return out[:i] + body + out[j + 1:], n


JS_RULES = [
    # default-language decision points, one per architecture found in the repo
    (re.compile(r"let currentLang = 'it';"), "let currentLang = 'en';"),
    (re.compile(r"var currentLang = 'it';"), "var currentLang = 'en';"),
    (re.compile(r"if\s*\(\s*stored\s*===\s*'en'\s*\)\s*setLang\('en'\);\s*else\s*setLang\('it'\);"),
     "if (stored === 'it') setLang('it'); else setLang('en');"),
    (re.compile(r"if\(s&&s!=='it'\)setLang\(s\)"), "if(s&&s!=='en')setLang(s)"),
    (re.compile(r"if\(saved&&saved!=='it'\)setLang\(saved\)"), "if(saved&&saved!=='en')setLang(saved)"),
    (re.compile(r"if \(stored === 'en'\) setLang\('en'\); else setLang\('it'\);"),
     "if (stored === 'it') setLang('it'); else setLang('en');"),
    # block-level toggles: make both directions explicit so they no longer
    # depend on which of .lang-it/.lang-en the stylesheet hides by default
    (re.compile(r"querySelectorAll\('\.lang-it'\)\.forEach\(el=>el\.style\.display=lang==='en'\?'none':''\)"),
     "querySelectorAll('.lang-it').forEach(el=>el.style.display=lang==='it'?'block':'none')"),
    (re.compile(r"querySelectorAll\('\.lang-it'\)\.forEach\(el => el\.style\.display = lang === 'en' \? 'none' : ''\)"),
     "querySelectorAll('.lang-it').forEach(el => el.style.display = lang === 'it' ? 'block' : 'none')"),
]

HTML_RULES = [
    (re.compile(r'<html lang="it"'), '<html lang="en"'),
    (re.compile(r'\.lang-en\{display:none\}'), '.lang-it{display:none}'),
    (re.compile(r'"inLanguage"\s*:\s*"it"'), '"inLanguage":"en"'),
    (re.compile(r'(<meta property="og:locale"[^>]*?)content="it_IT"'), r'\1content="en_US"'),
    # language switch: EN is the active button now
    (re.compile(r'<button class="lang-btn active"((?:(?!</button>).)*?)setLang\(\'it\'\)((?:(?!</button>).)*?)>IT</button>'),
     r'<button class="lang-btn"\1setLang(\'it\')\2>IT</button>'),
    (re.compile(r'<button class="lang-btn"((?:(?!</button>).)*?)setLang\(\'en\'\)((?:(?!</button>).)*?)>EN</button>'),
     r'<button class="lang-btn active"\1setLang(\'en\')\2>EN</button>'),
    (re.compile(r'(<button class="lang-btn(?: active)?"(?:(?!</button>).)*?setLang\(\'it\'\)(?:(?!</button>).)*?)aria-pressed="true"'),
     r'\1aria-pressed="false"'),
    (re.compile(r'(<button class="lang-btn(?: active)?"(?:(?!</button>).)*?setLang\(\'en\'\)(?:(?!</button>).)*?)aria-pressed="false"'),
     r'\1aria-pressed="true"'),
]

META_ATTR = {'description', 'og:description', 'og:title', 'og:locale',
             'twitter:description', 'twitter:title'}


def flip_meta(raw):
    """<meta ... data-en="X" content="Y"> -> content becomes X."""
    n = 0

    def sub(m):
        nonlocal n
        tag = m.group(0)
        key = re.search(r'(?:property|name)="([^"]+)"', tag)
        if not key or key.group(1) not in META_ATTR:
            return tag
        den = re.search(r'data-en="((?:[^"\\]|\\.)*)"', tag)
        con = re.search(r'content="((?:[^"\\]|\\.)*)"', tag)
        if not den or not con:
            return tag
        if con.group(1) == den.group(1):
            return tag
        n += 1
        return tag[:con.start(1)] + den.group(1) + tag[con.end(1):]

    return re.sub(r'<meta\b[^>]*>', sub, raw), n


def flip_dom(raw):
    """Render each data-en value into its element, outermost elements only."""
    loc = locate(raw)
    spans = [s for s in loc.spans]
    spans.sort(key=lambda s: -s[0])
    # guard: overlapping spans would mean nested data-en, which this site does not have
    ordered = sorted(spans, key=lambda s: s[0])
    for a, b in zip(ordered, ordered[1:]):
        if b[0] < a[1]:
            raise SystemExit('overlapping data-en spans — refusing to rewrite')
    out = raw
    n = 0
    for start, end, den in spans:
        cur = out[start:end]
        if html.unescape(cur).strip() == html.unescape(den).strip():
            continue
        out = out[:start] + den + out[end:]
        n += 1
    return out, n, loc.unbalanced


def process(path, report):
    raw = original = open(path, encoding='utf-8').read()

    raw, n_esc = repair_escapes(raw)
    drift = count_drift(raw)
    raw, n_meta = flip_meta(raw)
    raw, n_dom, unbalanced = flip_dom(raw)

    raw, n_res = fix_resolve_initial(raw)
    n_js = n_res
    for rx, rep in JS_RULES + HTML_RULES:
        raw, k = rx.subn(rep, raw)
        n_js += k

    report.append(dict(path=path, esc=n_esc, drift=drift, meta=n_meta,
                       dom=n_dom, rules=n_js, unbalanced=unbalanced,
                       changed=raw != original))
    return raw


def main():
    root = sys.argv[1]
    write = '--write' in sys.argv
    report = []
    for p in pages(root):
        out = process(p, report)
        if write:
            open(p, 'w', encoding='utf-8').write(out)

    print(f"{'page':<52}{'esc':>5}{'drift':>7}{'meta':>6}{'dom':>6}{'rules':>7}{'unbal':>7}")
    print('-' * 90)
    tot = dict(esc=0, drift=0, meta=0, dom=0, rules=0, unbalanced=0)
    for r in report:
        rel = os.path.relpath(r['path'], root)
        for k in tot:
            tot[k] += r[k]
        print(f"{rel:<52}{r['esc']:>5}{r['drift']:>7}{r['meta']:>6}{r['dom']:>6}{r['rules']:>7}{r['unbalanced']:>7}")
    print('-' * 90)
    print(f"{'TOTAL':<52}{tot['esc']:>5}{tot['drift']:>7}{tot['meta']:>6}{tot['dom']:>6}{tot['rules']:>7}{tot['unbalanced']:>7}")
    print()
    print('written' if write else 'DRY RUN — pass --write to apply')


if __name__ == '__main__':
    main()
