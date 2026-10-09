# Q5 — ricontrollo sulle versioni correnti (9/10/2026, sera)

Stessa VM, stesso repo di prova, stesso runner (`lab/p2-lab/scripts/run_q5_matrix.py`), clienti aggiornati in loco
alle release del 9/10 (nessun revert: lo snapshot `telemetry-20261009` resta quello di settembre).

| Client | 26–28/9 | 9/10 |
|---|---|---|
| Claude Code | 2.1.283 | **2.1.295** |
| Codex CLI | 0.157.1 | **0.162.0** |
| Gemini CLI | 0.61.0 | **0.63.0** |
| Qwen Code | 0.24.6 | **0.25.0** |
| Cursor CLI | 2026.09.26 | 2026.10.01 (già aggiornato da solo il 9/10) |
| Goose | 1.52.0 | 1.52.0 |

## Matrice (`q5-run-v2/20261009-211753`, 1 ripetizione; `q5-run-v2b/20261009-212921`, Gemini ×3)
Tutte le celle identiche a settembre (prompt, opzione preselezionata, canary prima = 0, dopo la decisione e alla
riapertura come prima), con una sola differenza:
- **Gemini 0.63.0, "Trust folder"**: il canary è partito nella stessa sessione in 1 caso su 4 (settembre, 0.61.0: 4 su 5).
  Nei 3 casi negativi la schermata dopo la scelta mostra `Skipping project agents due to untrusted folder` e la riga di stato
  resta non fidata; la fiducia è però salvata e alla riapertura il server parte 4 su 4. Possibile legame con la PR #29528
  di 0.63.0 ("propagate resolved folder trust state in headless mode"). Non è un peggioramento della sicurezza: in sessione
  il server non parte, parte alla riapertura di una cartella ormai fidata.
- Cursor CLI 2026.10.01: identico (2 eventi di avvio per sessione, come il 9/10 mattina).

## Modalità non interattiva (`q5-run-v2/ptest.log`, `*.out`)
| Comando | Avvii del canary | Note |
|---|---|---|
| `claude -p "say ok"` (2.1.295) | 3/3 | la sessione OAuth era scaduta: "Failed to authenticate", eppure il server è partito prima. ⚠️ Confondimento: l'ultima cella della matrice precedente aveva lasciato il progetto fidato in `~/.claude.json`; la misura pulita di settembre (3/3, prima del login) resta la fonte primaria, e la documentazione Anthropic conferma il comportamento |
| `claude --bare -p "say ok"` (2.1.295) | **0/3** | come documentato |
| `claude -p --setting-sources user "say ok"` (2.1.295) | **0/3** | come documentato |
| `gemini -p "say ok"` (0.63.0) | 3/3 **non valido** | cartella già fidata in `~/.gemini/trustedFolders.json` dalla matrice precedente |
| `qwen -p "say ok"` (0.25.0) | 0/3 | si ferma prima |
| `codex exec "say ok"` (0.162.0) | 0/3 | si ferma prima ("not inside a trusted directory") |

Da rifare, se serve per il preprint: `claude -p` e `gemini -p` con gli store di fiducia azzerati (manuale, nella VM).

## Aggiunta (22:34): `claude -p` pulito sulla 2.1.295 (`claude-p-clean/`)
Prima di ogni esecuzione: voce del progetto rimossa da `~/.claude.json` (stessa funzione `reset_claude()` del runner), repo
rigenerato, log svuotato; verificato che la voce resti assente anche dopo l'esecuzione. Esito: **3/3 avvii** del canary
(start + initialize nel log), con "Failed to authenticate: OAuth session expired". Conferma pulita della misura di settembre.
