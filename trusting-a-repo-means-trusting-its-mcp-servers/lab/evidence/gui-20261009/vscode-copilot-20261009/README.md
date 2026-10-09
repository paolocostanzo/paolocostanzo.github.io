# VS Code 1.139.1 + Copilot loggato — misura del 9/10/2026, 22:35–22:43 (VM agentlab, a mano via xdotool)

Repo rigenerato, log azzerato, stato workspace VS Code azzerato (`workspaceStorage` rimosso). Login GitHub fatto da Paolo
in Firefox dentro la VM; finestra "OS keyring" → *Use weaker encryption*; Firefox chiuso subito dopo.

| Passo | Cosa si vede | Canary |
|---|---|---|
| Apertura cartella | Restricted Mode, banner in alto, nessuna finestra modale | 0 |
| Ctrl+Alt+I (apri Chat) | **Finestra "Do you trust the authors of the files in this folder?"**: "AI features are currently only supported in trusted workspaces". Pulsanti: Manage · Cancel · **Trust Folder & Continue** (predefinito, evidenziato). Tenuta 25 s | 0 (`01-…png`) |
| Trust Folder & Continue | Restricted Mode sparisce; chat in modalità Agent, "Default permissions" | 0 dopo 12 s, 0 dopo 27 s (`02-…png`) |
| Prompt alla chat: "Use the q5-canary MCP tool…" | **Il server parte subito, senza domanda** (`start`+`initialize` 20:41:23, sorgente `.vscode/mcp.json`; il `.mcp.json` in radice non parte). La chat mostra la scheda "Run canary_noop — q5-canary (MCP Server)" con **Allow in this Session** (predefinito) · Skip | 1 entro 10 s (`03-…png`) |
| Skip | nessuna chiamata al tool; il server resta avviato | 1, calls=0 (`04-…png`) |

Lettura: con Copilot loggato, VS Code non avvia il server all'apertura né alla fiducia, ma **lo avvia senza chiedere nel
momento in cui la chat vuole usare un suo tool**; la domanda all'utente riguarda la *chiamata* del tool, non l'avvio del
programma, che a quel punto è già in esecuzione. La fiducia sul workspace viene chiesta solo quando si apre la chat
(non all'apertura della cartella), con l'opzione che dà fiducia come predefinita.
Una sola esecuzione (nessuna ripetizione da snapshot pulito).
