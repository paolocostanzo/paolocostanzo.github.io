# Cursor IDE 3.22.7 loggato — misura del 9/10/2026, 22:55–23:00 (VM agentlab, a mano via xdotool)

Login Cursor fatto da Paolo nel browser dell'host tramite il link `loginDeepControl` catturato (nessun browser nella VM);
VM riavviata a forza dopo un blocco per memoria (Firefox + Cursor), poi Cursor aperto da solo su un repo rigenerato,
log azzerato, stato del progetto in `~/.cursor/projects/` rimosso. Finestra "OS keyring" → *Use weaker encryption*.

| Passo | Cosa si vede | Canary |
|---|---|---|
| Apertura cartella (`cursor ~/q5-repo`) | **Nessuna domanda di fiducia**, nessuna modalità ristretta; pannello Agent pronto (`01-…png`) | 0 dopo 30 s |
| Prompt all'agente: "Use the q5-canary MCP tool…" | L'agente legge README e marker-server.mjs senza chiedere, cerca il server nel catalogo, prova a caricarlo e riceve `MCP server 'q5-canary' not found`; conclude che Cursor **non ha caricato** il server del progetto (`02-`, `03-…png`) | 0 dopo 45 s |
| Settings → Customize → MCPs | Elenco: `Origin (User) 13 tools enabled`; **`q5-canary (q5-repo) · Disabled`** (`04-…png`) | 0 |
| Clic sulla voce | Finestra "Configure q5-canary · 1 source": interruttore **q5-repo · .cursor/mcp.json** spento; "Local · Disconnected". **Il comando non viene mostrato** (`05-…png`) | 0 |
| Interruttore acceso (controllo positivo) | Server avviato subito: `start`+`initialize` 20:59:42; "Local · Connected"; "1 tool enabled"; tool `canary_noop` con "Writes: Allow all" (`06-…png`) | 1 entro 5 s |

Lettura: Cursor IDE non chiede fiducia all'apertura e tiene i server dichiarati dal progetto **disabilitati** finché l'utente
non li accende dalle impostazioni; l'accensione avvia il programma senza mostrare il comando. È il comportamento più
conservativo dopo VS Code senza chat, ma la schermata di abilitazione dà meno informazioni di Qwen (comando completo) e
della CLI di Cursor (`command: node`). Una sola esecuzione.
