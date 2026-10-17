# GUI Q3 — Cursor IDE, segnale list_changed, ripetizione gui1 (17/10/2026)

Snapshot pulito q3-base-20261015, orologio allineato, Cursor avviato con --disable-gpu. Pilotato da Claude Code con xdotool.

- 13:00:39 avvio Cursor IDE
- 13:00:59 chiusa la finestra promozionale (X)
- 13:01:06 fase 1: frase inviata
- 13:01:24 fase 1 finita (screenshot 03)
- 13:01:24 fase 2: definizione A→B con notifica (list_changed); attesa 12 s
- 13:01:40 fase 2: frase inviata di nuovo
- 13:01:58 fase 2 finita (screenshot 06)
- 13:02:27 fase 2: dopo la notifica nessun avviso (screenshot 04, 05); server: tools/list immediata, servita B; seconda chiamata senza domande (screenshot 06)
- 13:02:42 fase 3: sessione grafica riavviata (lightdm), Cursor chiuso con essa
- 13:02:43 fase 3: Cursor riaperto (--disable-gpu)
- 13:03:11 fase 3: chat nuova (+), frase inviata
- 13:03:56 fase 3 finita: nessuna domanda, risposta 'ok' (screenshot 08); server: avvio, tools/list ha servito B, tools/call riuscita
