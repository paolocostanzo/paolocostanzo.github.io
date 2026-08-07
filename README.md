# paolocostanzo.github.io

Sito di ricerca personale — **NonèCostanzo**. HTML statico scritto a mano, zero
build step, zero dipendenze runtime. Pubblicato su GitHub Pages.

> Il sito vetrina/commerciale è un progetto separato: <https://paolocostanzo.com>.
> Qui vivono solo research report, lab riproducibili, field note e writeup CTF.

## Struttura

```
index.html                     homepage (CSS + JS inline, IT/EN via data-it/data-en)
<slug>/index.html              un articolo per cartella, self-contained
<slug>/og.png                  Open Graph image 1200x630
ctf/index.html                 hub CTF
ctf/<serie>/index.html         hub di serie
ctf/<serie>/<room>/index.html  singolo writeup
feed.xml                       RSS — alimenta anche il README del profilo GitHub
sitemap.xml robots.txt         SEO (generati/aggiornati via tools/)
tools/                         generatori — non pubblicati sul sito
```

Ogni pagina è autonoma: niente CSS o JS condivisi via file esterni. Costa un po'
di duplicazione ma il sito resta servibile da qualsiasi CDN senza pipeline.

## Deploy

Push su `main` → workflow `.github/workflows/deploy-pages.yml` → GitHub Pages.

Il deploy passa da GitHub Actions e **non** dalla build Jekyll classica: quella
aveva un limite di 10 build/ora che bloccava le giornate con più pubblicazioni.
`.nojekyll` disattiva il processing Liquid.

Il workflow rimuove `tools/`, `README.md` e `.github/` dall'artifact: sul sito
finiscono solo HTML e asset.

`concurrency.cancel-in-progress` è **false** di proposito — annullare una
pubblicazione Pages a metà lascia l'ambiente in stato incoerente. I push
ravvicinati si accodano.

## Dopo aver pubblicato un articolo

1. aggiungere la card in `index.html` (e togliere la classe `archived` dalle
   card che devono restare in vista — le altre finiscono in archivio);
2. aggiungere l'`<item>` in `feed.xml` — **passaggio obbligatorio**: il README
   del profilo GitHub legge questo feed una volta al giorno, se il feed è fermo
   il profilo mostra contenuti vecchi;
3. generare l'OG image e rigenerare la sitemap:

```bash
python3 tools/generate-og.py <chiave>   # chiavi nel MANIFEST dello script
python3 tools/generate-sitemap.py
```

## Tools

| Script | Cosa fa |
|---|---|
| `tools/generate-og.py` | Genera le OG image 1200x630 nello stile del sito. Il titolo si auto-riduce per stare in due righe: senza questo controllo il testo esce dal canvas. |
| `tools/generate-sitemap.py` | Rigenera `sitemap.xml` dalle pagine tracciate da git, con `lastmod` preso dall'ultimo commit di ogni pagina. |

Richiedono Python 3 e Pillow. I font sono risolti per piattaforma (Arial/Menlo
su macOS, DejaVu/Liberation su Linux): il sito viene aggiornato da entrambe.

## Contatti di sicurezza

`.well-known/security.txt` → <me@paolocostanzo.com>
