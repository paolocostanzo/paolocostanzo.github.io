# GUI Q3 — VS Code + Copilot, segnale list_changed, ripetizione gui2 (17/10/2026)

Ripetizione completa dopo l incidente del display in gui1. Snapshot pulito q3-base-20261015, orologio allineato, VS Code avviato con --disable-gpu (solo resa grafica). Pilotato da Claude Code con xdotool.

- 12:49:50 avvio VS Code (--disable-gpu)
- 12:50:12 cartella fidata (Manage → Trust), finestra chiusa
- 12:50:18 fase 1: frase inviata
- 12:50:55 richiesta di permesso uguale (Run q3_noop, descrizione A, avviso sui server MCP); menu aperto
- 12:51:13 scelto 'Always Allow'
- 12:51:25 fase 1 finita (screenshot 05)
- 12:51:25 fase 2: definizione A→B con notifica (list_changed); attesa 12 s
- 12:51:41 fase 2: frase inviata di nuovo
- 12:51:56 fase 2 finita (screenshot 08)
- 12:52:02 fase 3: VS Code chiuso (Ctrl+Q)
- 12:52:55 fase 3: VS Code riaperto (--disable-gpu), frase inviata
- 12:53:13 fase 3 finita (screenshot 10)
- 12:53:31 fase 3: la frase non era entrata (finestra non ancora pronta, screenshot 10); nuovo invio
- 12:54:25 fase 3: frase inviata (screenshot 11)
- 12:55:04 fase 3 NON OSSERVATA: dopo la riapertura il display della VM si è bloccato di nuovo (dmesg: 10 righe '[TTM] Buffer eviction failed', xdotool non risponde); il server non è ripartito. --disable-gpu non evita il problema. Fasi 1-2 valide.
