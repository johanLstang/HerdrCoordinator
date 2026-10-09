# F-15: explicit task_start och recovery

`TaskStartService.start(actor, epic_run_id, spec)` tar en [lokal version-1-spec](F-14-kontrakt.md). Betrodd Integration måste vara bunden till samma projekt och aktiva, F-05-registrerade epic. Inga roll-, session- eller verifieringsbooleans tas från Worker-/MCP-argument.

## Start och persistens

1. Validera taskspec, epic, scope och beroenden. Lokala taskberoenden kräver F-07:s registrerade Done-/review-/Git-/testleverans. Externa villkor kräver ett betrott operatörsinstallerat preflight-anrop som returnerar exakt True; annars startas inget.
2. Håll samma repo-lås som integration och en SQLite-transaktion. F-05:s `prepare_only` sparar skapandeintent och aktuell epicbas utan Git-mutation. Spara unik run, reserverad slot, immutable spec/prompt med SHA256/version och task_start-operation; övergå PLANNED → CLAIMED. För stor prompt avvisas före commit av transaktionen. Inget worktree eller runtime skapas före claim.
3. Skapa/verifiera Git via F-05 och spara GIT_READY. Ändrad sparad epicbas eller främmande resurser kräver avstämning, inte adoption eller ny run.
4. Släpp Gitlåset. F-11 sparar varje Herdr-startsteg och övergår CLAIMED → STARTING. Known/unknown outcomes hanteras av samma underoperation.
5. F-12 levererar den sparade F-14-prompten en gång. WORKING kräver den korrelerade native ACK:n från rätt session/turn, aldrig en skärmtext, lyckad startprocess eller transportretur ensam. Parentoperationen blir SUCCEEDED/WORKING när ACK bekräftats.

F-15 levererades ursprungligen med en Worker i fas 4. Från [F-28](../scheduling/F-28-slots.md) respekterar alla tre flöden samma `max_workers = 1` eller `2` och atomiska reservationer. En befintlig claim räknas även under start, review/fix eller okänt runtimeutfall. F-13:s oberoende stoppbevis krävs för att frigöra startad eller möjligen startad runtime. Ett verifierat F-15-fel före varje runtime-startintent och bindning kan däremot frigöra sin claim; samma run, operation och bas återanvänds med en ny ledig slot vid retry. F-15 utför ingen parkering, merge eller Done.

Schema 2 är oförändrat. Operationsnyckeln `(project, task_start, task_id)` och det befintliga unika taskägarskapet hindrar nya runs. Spec/prompt/epic/run/branch/cwd/base/runtime-konfiguration kan inte bytas genom retry. Parentsteg går framåt; varje känd extern resurs finns i F-05/F-11/F-12-journalen. SQLite-anslutningar är per servicetråd, repo-låset koordinerar processer; externa manuella skrivare omfattas inte.

## Återförsök

| Avbrott | Nästa anrop |
| --- | --- |
| Före claim-commit | Ingen claim/resurs finns; vanlig start kan ske. |
| Efter claim/före eller efter Git | Återanvänd run, bas och skapandeintent. Efter verifierat fel utan runtimeintent/bindning reserveras en ledig slot på nytt. Annars bevaras reservationen. Verifiera resurserna. |
| Workspace-anrop utan känt svar | Bevara STARTING och slot; manuell avstämning. Skapa inget nytt workspace. |
| Agentstart med okänt svar | Observera exakt känt namn/pane/process; skicka inte ny start. |
| Prompt utan bekräftad ACK | Observera sparad operation även när Worker är busy. Skicka inte om. Timeout/dialog kräver åtgärd. |
| ACK sparad, parentcheckpoint saknas | Läs samma F-12-bevis och slutför parentcheckpoint. |
| Parent SUCCEEDED | Returnera EXISTING och samma run. Ingen ny runtime/prompt. |

## MCP och startkommando

Utan runtimekonfiguration finns bara de tidigare läsverktygen. Operatören kan aktivera task_start med en **explicit vald** Herdr-session och skyddad Integration-principal:

```bash
uv run --locked herdr-coordinator --config /path/herdr.local.toml --mcp \
  --principal /path/operator/integration.json --herdr-session explicit-test-session \
  --worker-sandbox workspace-write
```

Herdr måste redan vara tillgängligt i den faktiska `HERDR_ENV=1`-miljön. Ingen server startas implicit. MCP-input är `{project_id, epic_run_id, task: <LocalTaskSpec>}`: inline JSON, ingen godtycklig filread eller agentvald principal/runtimekonfiguration. Fel ger TASK_START_UNVERIFIED och känd task_run_id/stage när intent finns. Alla anrop bindas till anslutningens registrerade Integration-identitet.

F-15:s prov använder verkligt temporärt Git/SQLite och en kontrollerad Herdr/Codex-adapter. Det är ingen faktisk Worker-implementation. Full Worker-isolering och verklig leverans verifieras i F-17 före autonom integration; rapportkontroller levereras i F-16.
