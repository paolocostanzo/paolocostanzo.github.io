# GUI Q3 — VS Code + Copilot, segnale list_changed, ripetizione gui1 (17/10/2026)

Pilotato da Claude Code con xdotool via SSH; snapshot pulito q3-base-20261015; orologio della VM allineato all host.

- 12:42:12 avvio VS Code
- 12:42:44 cartella fidata (Trust), finestra chiusa
- 12:42:50 fase 1: frase inviata
- 12:43:19 richiesta di permesso uguale al run precedente; aperto il menu delle opzioni
- 12:43:37 scelto 'Always Allow'
- 12:43:50 fase 2: definizione A→B con notifica (list_changed); attesa 12 s
- 12:44:15 dopo la notifica: nessun avviso a schermo (screenshot 06, 07); server: tools/list immediata, servita B
- 12:44:18 fase 2: frase inviata di nuovo
- 12:44:44 fase 2 finita: nessuna domanda né avviso, risposta 'ok' (screenshot 08); server: tools/call con B già servita
- 12:44:50 fase 3: VS Code chiuso (Ctrl+Q)
- 12:47:58 INCIDENTE: al riavvio di VS Code il display della VM si è bloccato (dmesg: '[TTM] Buffer eviction failed', memoria video esaurita); un xdotool è rimasto appeso; terminati VS Code e xdotool; riavvio di lightdm. Fasi 1-2 già registrate e non toccate.
- 12:49:18 chiuso come gui1 INCOMPLETO: fasi 1-2 valide (notifica consegnata, B richiesta subito, chiamata riuscita senza nulla a schermo); fase 3 non eseguita per il blocco del display. Il run si ripete da capo come gui2, con editor avviati con --disable-gpu (solo resa grafica).
