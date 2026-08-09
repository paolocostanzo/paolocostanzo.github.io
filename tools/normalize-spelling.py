#!/usr/bin/env python3
"""
Settle the site on US spelling in its English prose.

Thirteen of fourteen CTF rooms mixed conventions, and the recurring section
heading of a single fourteen-part series split between "Defence" and "Defense".
The sitewide footer managed to contradict itself on two adjacent lines. One mixed
page reads as a typo; a whole series reads as carelessness, which is expensive on
a site whose product is credibility.

Deliberately left alone:
  - data-it values — that is the Italian version
  - <pre> and <code> — command output and source are transcripts, not prose
  - id/class/href attribute values — ids are in-page anchor targets
  - "Business & Human Rights Resource Centre", a proper noun
  - analysis / analyses / analyst, identical in both conventions
"""
import re
import subprocess
import sys

PAIRS = [
    ('authorised', 'authorized'), ('authorise', 'authorize'), ('authorising', 'authorizing'),
    ('defence', 'defense'),
    ('normalised', 'normalized'), ('normalises', 'normalizes'),
    ('normalising', 'normalizing'), ('normalise', 'normalize'), ('normalisation', 'normalization'),
    ('organised', 'organized'), ('organise', 'organize'), ('organising', 'organizing'),
    ('recognised', 'recognized'), ('recognises', 'recognizes'),
    ('recognising', 'recognizing'), ('recognise', 'recognize'),
    ('behaviour', 'behavior'), ('behaviours', 'behaviors'),
    ('sanitised', 'sanitized'), ('sanitises', 'sanitizes'),
    ('sanitising', 'sanitizing'), ('sanitise', 'sanitize'), ('sanitisation', 'sanitization'),
    ('customised', 'customized'), ('customises', 'customizes'),
    ('customising', 'customizing'), ('customise', 'customize'), ('customisation', 'customization'),
    ('serialised', 'serialized'), ('serialises', 'serializes'),
    ('serialising', 'serializing'), ('serialise', 'serialize'),
    ('generalised', 'generalized'), ('generalises', 'generalizes'),
    ('generalising', 'generalizing'), ('generalise', 'generalize'),
    ('initialised', 'initialized'), ('initialise', 'initialize'),
    ('optimised', 'optimized'), ('optimise', 'optimize'), ('optimisation', 'optimization'),
    ('summarised', 'summarized'), ('summarise', 'summarize'),
    ('prioritised', 'prioritized'), ('prioritise', 'prioritize'),
    ('visualised', 'visualized'), ('visualise', 'visualize'),
    ('utilised', 'utilized'), ('utilise', 'utilize'),
    ('minimised', 'minimized'), ('minimise', 'minimize'),
    ('maximised', 'maximized'), ('maximise', 'maximize'),
]
# longest first so "normalising" is not eaten by "normalise"
PAIRS.sort(key=lambda p: -len(p[0]))

KEEP = re.compile(r'Business\s*&(?:amp;)?\s*Human\s+Rights[^<]*?Centre', re.I)


def protected_spans(raw):
    """Regions this pass must not touch."""
    spans = []
    for rx in (r'data-it="((?:[^"\\]|\\.)*)"',
               r"data-it='((?:[^'\\]|\\.)*)'"):
        for m in re.finditer(rx, raw):
            spans.append((m.start(1), m.end(1)))
    for m in re.finditer(r'(?is)<(pre|code)\b.*?</\1>', raw):
        spans.append((m.start(), m.end()))
    for m in re.finditer(r'\b(?:id|class|href|src)\s*=\s*("[^"]*"|\'[^\']*\')', raw):
        spans.append((m.start(1), m.end(1)))
    for m in KEEP.finditer(raw):
        spans.append((m.start(), m.end()))
    return spans


def match_case(src, dst):
    if src.isupper():
        return dst.upper()
    if src[0].isupper():
        return dst[0].upper() + dst[1:]
    return dst


def convert(raw):
    spans = protected_spans(raw)

    def safe(pos):
        return not any(a <= pos < b for a, b in spans)

    counts = {}
    for gb, us in PAIRS:
        rx = re.compile(r'\b' + gb + r'\b', re.I)
        out, last, pieces = [], 0, 0
        for m in rx.finditer(raw):
            if not safe(m.start()):
                continue
            out.append(raw[last:m.start()])
            out.append(match_case(m.group(0), us))
            last = m.end()
            pieces += 1
        if pieces:
            out.append(raw[last:])
            raw = ''.join(out)
            spans = protected_spans(raw)   # offsets shifted
            counts[gb] = pieces
    return raw, counts


def main():
    write = '--write' in sys.argv
    files = subprocess.run(['git', 'ls-files', '*.html'],
                           capture_output=True, text=True).stdout.split()
    grand = {}
    for f in files:
        raw = open(f, encoding='utf-8').read()
        out, counts = convert(raw)
        if not counts:
            continue
        total = sum(counts.values())
        print(f'{total:>4}  {f}   ' + ', '.join(f'{k}x{v}' for k, v in counts.items()))
        for k, v in counts.items():
            grand[k] = grand.get(k, 0) + v
        if write:
            open(f, 'w', encoding='utf-8').write(out)
    print(f'\n{sum(grand.values())} replacements: ' + ', '.join(f'{k}={v}' for k, v in sorted(grand.items())))
    print('written' if write else 'DRY RUN — pass --write')


if __name__ == '__main__':
    main()
