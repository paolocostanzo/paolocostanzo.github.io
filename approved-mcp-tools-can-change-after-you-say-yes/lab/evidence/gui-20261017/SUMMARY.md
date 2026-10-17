# Q3 — editor grafici (VS Code + Copilot, Cursor IDE), 17/10/2026

Celle secondarie fatte a mano come previsto dal protocollo (D5/D16: "GUI a mano sulle stesse celle; meno di 5 ripetizioni →
dichiararlo"). **Una ripetizione per editor e per segnale** (più una ripetizione supplementare per VS Code con notifica, vedi sotto).
Pilotate da Claude Code con `xdotool` via SSH dentro la VM, su richiesta di Paolo, che guardava la console; screenshot con
`virsh screenshot`. Stesso server `q3-lab`, stessa frase fissa, stessi tre momenti dei client da terminale. Ogni ripetizione parte
dallo snapshot pulito `q3-base-20261015`, con l'orologio della VM allineato all'host (era fermo allo snapshot). Prove con SHA-256
in ogni cartella (`SHA256SUMS`), registro minuto per minuto in `LOG.md`.

| Editor | Protocollo | Prima chiamata (predefinito) | Cambio, nessun segnale | Cambio + notifica | Dopo il riavvio (esplorativo) |
|---|---|---|---|---|---|
| VS Code 1.139.1 + Copilot Chat | legacy | **chiede**: "Run q3_noop – q3-lab (MCP Server)" mostra la descrizione A e l'avviso "MCP servers or malicious conversation content may attempt to misuse 'Code' through tools"; sei opzioni da "Allow Once" ad "Always Allow Tools from q3-lab"; scelto **Always Allow** | stale (nessun `tools/list`, A ancora in uso) | **silent accept**: `tools/list` 4 ms dopo la notifica, servita B, chiamata riuscita, niente a schermo (gui1 e gui2, fasi 1–2) | silent accept dopo il run senza segnale (B servita, nessuna domanda); **non osservato** dopo il run con notifica (vedi incidente) |
| Cursor IDE 3.22.7 | legacy | **nessuna domanda**: il pannello Agent ha chiamato il tool senza chiedere (impostazioni dell'IDE presenti nello snapshot) | stale | **silent accept**: `tools/list` 6 ms dopo la notifica, servita B, niente a schermo | silent accept (entrambi i run) |

Confronto con i client da terminale (matrice del 15/10):
- VS Code e Cursor IDE si comportano come Claude Code: con la notifica rileggono e usano B senza dire nulla. Cursor CLI invece
  la notifica la riceve e la ignora (stale).
- Nessuno dei due editor chiede di nuovo il permesso, in nessuna fase. Come per i sei client da terminale, la clausola 1 di H3
  regge anche sugli editor.
- Cursor IDE alla prima chiamata non chiede nulla, mentre Cursor CLI chiede "Allowlist MCP Tool". Va verificato se dipende
  dalle impostazioni salvate nello snapshot (sessione del 9/10) o dal comportamento predefinito dell'IDE: nel post scrivere
  "nelle impostazioni del nostro snapshot".

**Incidente (VS Code, notifica).** Alla seconda apertura di VS Code nella stessa sessione grafica il display della VM si è
bloccato: nel kernel compare `[TTM] Buffer eviction failed`, cioè la memoria video esaurita. È successo in due ripetizioni
(gui1, gui2), anche con `--disable-gpu`.
- Le fasi 1–2 di entrambe sono valide e concordi.
- La fase 3 non è stata osservata.
- Per Cursor IDE la fase 3 si è fatta riavviando la sessione grafica (lightdm) prima di riaprire l'editor: nessun blocco.

**Altre note.**
- In VS Code la cartella è stata resa fidata (Restricted Mode → Trust) prima di qualunque chiamata, come prevede D16 punto 2.
- In Cursor IDE `Ctrl+Q` non chiude l'applicazione. La chiusura è avvenuta con il riavvio della sessione grafica.
- All'avvio Cursor IDE apre il server, legge la lista, lo chiude e lo riapre. La seconda istanza non chiede la lista: usa
  quella già letta.
- Telemetria (audit, journal, log dei client) salvata per ogni run. Riguarda Q4 ed è esplorativa: la deviazione per Q4 non è
  ancora scritta.
