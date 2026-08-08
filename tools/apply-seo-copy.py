#!/usr/bin/env python3
"""
Apply the SEO copy set to the Hacker Holidays writeups.

Rewrites, per page, the bilingual pair AND the rendered value of: <title>, the
meta description, og:title / og:description, twitter:title / twitter:description,
the <h1>, and the headline / description / keywords inside the TechArticle JSON-LD.

Input is the JSON produced by the copywriting panel:
  {"pages": [{"slug", "title_en", "title_it", "desc_en", "desc_it",
              "h1_en", "h1_it", "keywords"}, ...]}
"slug" is the room folder, or "hub" for the series index.

Edits are surgical string splices: this HTML is hand-written and a parser
round-trip rewrites hundreds of lines per file for nothing.
"""
import html
import json
import os
import re
import sys

BASE = 'ctf/hacker-holidays-2026'


def esc(value):
    """Escape for use inside a double-quoted HTML attribute."""
    return value.replace('&', '&amp;').replace('"', '&quot;')


def set_bilingual_tag(raw, tag, it, en, attr_filter=None):
    """<tag ... data-it=".." data-en="..">rendered</tag> — all three updated."""
    pattern = re.compile(r'<' + tag + r'\b([^>]*)>(.*?)</' + tag + r'>', re.S | re.I)

    def sub(m):
        attrs = m.group(1)
        if attr_filter and not attr_filter(attrs):
            return m.group(0)
        if 'data-en=' not in attrs:
            return m.group(0)
        attrs = re.sub(r'data-it="(?:[^"\\]|\\.)*"', 'data-it="%s"' % esc(it), attrs)
        attrs = re.sub(r'data-en="(?:[^"\\]|\\.)*"', 'data-en="%s"' % esc(en), attrs)
        return '<%s%s>%s</%s>' % (tag, attrs, html.escape(en, quote=False), tag)

    return pattern.subn(sub, raw, count=1)


def set_meta(raw, key, it, en):
    """<meta name|property="key" data-it=".." data-en=".." content=".."/>"""
    def sub(m):
        tag = m.group(0)
        k = re.search(r'(?:property|name)="([^"]+)"', tag)
        if not k or k.group(1) != key:
            return tag
        tag = re.sub(r'data-it="(?:[^"\\]|\\.)*"', 'data-it="%s"' % esc(it), tag)
        tag = re.sub(r'data-en="(?:[^"\\]|\\.)*"', 'data-en="%s"' % esc(en), tag)
        tag = re.sub(r'content="(?:[^"\\]|\\.)*"', 'content="%s"' % esc(en), tag)
        return tag

    return re.subn(r'<meta\b[^>]*>', sub, raw)


def set_jsonld(raw, en_title, en_desc, keywords):
    """Only the TechArticle / CollectionPage block, and only three fields."""
    def sub(m):
        body = m.group(1)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return m.group(0)
        if data.get('@type') not in ('TechArticle', 'CollectionPage'):
            return m.group(0)
        if 'headline' in data:
            data['headline'] = en_title
        elif 'name' in data:
            data['name'] = en_title
        data['description'] = en_desc
        if keywords:
            data['keywords'] = keywords
        return '<script type="application/ld+json">\n%s\n  </script>' % json.dumps(
            data, ensure_ascii=False, indent=2)

    return re.subn(r'(?is)<script type="application/ld\+json">(.*?)</script>', sub, raw)


def apply_page(root, entry):
    slug = entry['slug']
    rel = f'{BASE}/index.html' if slug == 'hub' else f'{BASE}/{slug}/index.html'
    path = os.path.join(root, rel)
    if not os.path.exists(path):
        return rel, 'NOT FOUND', 0

    raw = open(path, encoding='utf-8').read()
    changed = 0

    raw, n = set_bilingual_tag(raw, 'title', entry['title_it'], entry['title_en'])
    changed += n
    raw, n = set_bilingual_tag(raw, 'h1', entry['h1_it'], entry['h1_en'])
    changed += n

    for key, it, en in (
        ('description', entry['desc_it'], entry['desc_en']),
        ('og:description', entry['desc_it'], entry['desc_en']),
        ('twitter:description', entry['desc_it'], entry['desc_en']),
        ('og:title', entry['title_it'], entry['title_en']),
        ('twitter:title', entry['title_it'], entry['title_en']),
    ):
        raw, n = set_meta(raw, key, it, en)
        changed += n

    raw, n = set_jsonld(raw, entry['title_en'], entry['desc_en'], entry.get('keywords', ''))
    changed += n

    if '--write' in sys.argv:
        open(path, 'w', encoding='utf-8').write(raw)
    return rel, 'ok', changed


def main():
    root, copy_path = sys.argv[1], sys.argv[2]
    pages = json.load(open(copy_path, encoding='utf-8'))['pages']
    print(f"{'page':<46}{'state':>10}{'edits':>7}{'title len':>11}")
    print('-' * 74)
    for entry in pages:
        rel, state, n = apply_page(root, entry)
        warn = '  <== long' if len(entry['title_en']) > 75 else ''
        print(f"{rel.replace(BASE + '/', ''):<46}{state:>10}{n:>7}{len(entry['title_en']):>11}{warn}")
    print('\n' + ('written' if '--write' in sys.argv else 'DRY RUN — pass --write to apply'))


if __name__ == '__main__':
    main()
