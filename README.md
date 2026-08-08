# paolocostanzo.github.io

Personal security research site — **NonèCostanzo**. Hand-written static HTML, no
build step, no runtime dependencies. Published on GitHub Pages.

> The commercial/portfolio site is a separate project: <https://paolocostanzo.com>.
> This repo holds only research reports, reproducible labs, field notes and CTF
> writeups.

## Layout

```
index.html                     homepage (inline CSS + JS, EN/IT via data-en/data-it)
<slug>/index.html              one article per folder, self-contained
<slug>/og.png                  Open Graph image 1200x630
ctf/index.html                 CTF hub
ctf/<series>/index.html        series hub
ctf/<series>/<room>/index.html individual writeup
feed.xml                       RSS — also feeds the GitHub profile README
sitemap.xml robots.txt         SEO (generated/updated via tools/)
tools/                         generators — never published to the site
```

Every page is standalone: no shared CSS or JS pulled in from external files. It
costs some duplication, but the site stays serveable from any CDN with no
pipeline in front of it.

## Languages

Pages are bilingual **English/Italian**, driven by attributes rather than by
separate URLs. Each prose element carries both `data-en` and `data-it`, and an
in-page toggle (`setLang()`, persisted in `localStorage`) swaps them in place.

- **English is the rendered/primary language.** It is what sits in the DOM on
  first paint, what crawlers see, and what `<html lang>` declares.
- **Italian is one click away** via the language toggle — same URL, no reload.
- **URLs stay clean.** There is no `/en/` or `/it/` path prefix and no separate
  per-language page; a link points at one canonical URL regardless of the
  reader's language.

Because there is only one URL per page, the page `<title>` and `<h1>` are
written for **English search queries** — that is the text search engines index.
The Italian variant lives in `data-it` and is served to the reader, not to the
crawler.

When adding or editing prose, keep both attributes in sync. An element with a
`data-en` but no `data-it` (or vice versa) will simply stop switching.

## Deploy

Push to `main` → `.github/workflows/deploy-pages.yml` → GitHub Pages.

Deployment runs through GitHub Actions and **not** through the classic Jekyll
build: that path is capped at 10 builds/hour, which used to block any day with
more than a handful of publications. `.nojekyll` disables Liquid processing.

The workflow strips `tools/`, `README.md` and `.github/` out of the artifact —
only HTML and assets reach the site.

`concurrency.cancel-in-progress` is **false** on purpose: killing a Pages
publication halfway through leaves the environment in an inconsistent state.
Back-to-back pushes queue up instead.

## After publishing an article

1. add the card to `index.html` (and drop the `archived` class from the cards
   that should stay visible — the rest fall through to the archive);
2. add the `<item>` to `feed.xml` — **this step is mandatory**: the GitHub
   profile README reads this feed once a day, so a stale feed means a stale
   profile;
3. generate the OG image and regenerate the sitemap:

```bash
python3 tools/generate-og.py <key>   # keys live in the script's MANIFEST
python3 tools/generate-sitemap.py
```

## Tools

| Script | What it does |
|---|---|
| `tools/generate-og.py` | Generates the 1200x630 OG images in the site's visual style. The title auto-shrinks to fit two lines: without that check the text overflows the canvas. |
| `tools/generate-sitemap.py` | Regenerates `sitemap.xml` from the git-tracked pages, taking `lastmod` from each page's most recent commit. |

Both require Python 3 and Pillow. Fonts are resolved per platform (Arial/Menlo
on macOS, DejaVu/Liberation on Linux) because the site gets updated from both.

## Security contact

`.well-known/security.txt` → <me@paolocostanzo.com>
