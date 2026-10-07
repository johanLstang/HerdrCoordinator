# Verifiering av Herdr/Codex-gränssnitt — F-10

Datum: 2026-10-08. Status: **ofullständig — trust-beslut krävs**. Detta är ett underlag för F-11–F-13, inte ett påstående om färdig runtimeintegration.

## Miljö och avgränsning

Verkliga binärer: Herdr 0.9.3 (`/home/stang/.local/bin/herdr`), Codex CLI 0.161.0 (`/home/stang/.local/bin/codex`), Linux aarch64/Raspberry Pi OS 12. Codex `login status` bekräftade befintlig ChatGPT-inloggning. Credentials lästes inte. Herdr private protocol 22 var kompatibelt.

Kontrollkontexten hade verkliga `HERDR_ENV=1`, workspace `w3`, tab `w3:t1` och pane `w3:p1`. Inga kontrollvariabler fabricerades. Testet använder en separat namngiven Herdr-server `hc-f10-20261008`; dess socket är `/home/stang/.config/herdr/sessions/hc-f10-20261008/herdr.sock`. ID:n nedan gäller bara denna server. Produktions-/användarservern stoppas inte.

Ofarlig Git-testrepo: `.herdr/probes/f10/repo` i `task/e03-f10`-worktreet. Den initialiserades med en README. En separat sandboxprovfil tillkom i repon; inga produktkällor ändrades. Lokala råprov/schema är ignorerade under `.herdr/probes/f10`; sanerade fakta finns här.

## Transport och resursidentitet

Herdr CLI:s kontrollkommandon returnerar JSON `{id,result}` eller `{id,error:{code,message}}`. Använd argv-listor, explicit `--session` och faktiskt återlästa ID:n. Exitkod och JSON-resultat kontrolleras tillsammans. Här är `id` ett anrops-ID (`cli:...`), inte en idempotensnyckel. Återförsök på okänt nät-/processutfall får inte automatiskt skapa en ny workspace/agent eller skicka samma prompt igen.

Workspace/pane-ID är serverlokala och måste lagras tillsammans med server/sessionidentiteten. Agentnamn följer aktuell paneoccupant och ersätter inte ett beständigt Codex-session-ID. Process-PID är ett ögonblicksvärde och kan återanvändas. Produktens run-ID, roll och ownership fastställs av orchestratorn, inte av agentnamn eller CLI-argument.

## Verkliga Herdr-prov

Alla kontrollkommandon nedan har prefix `herdr --session hc-f10-20261008`. Start av separat server: `herdr --session hc-f10-20261008 server`. Den långlivade serverprocessen startade och publicerade sin separata socket.

| Operation | Verifierat anrop | Faktiskt resultat |
| --- | --- | --- |
| Skapa workspace/pane | `workspace create --cwd <absolut-testrepo> --label hc-f10-probe --no-focus` | `workspace_created`; workspace `w1`, tab `w1:t1`, pane `w1:p1`, terminal `term_65d4844f4bcd91`; cwd exakt testrepo. Första workspace i tom testserver var focused trots `--no-focus`; inget fokus i användarservern ändrades. |
| Återläs workspace | `workspace get w1` | Samma workspace, en tab och en pane; aktuell agentstatus blocked. |
| Starta agent | `agent start hc-f10-codex --kind codex --pane w1:p1 --timeout 30000 -- --sandbox read-only --ask-for-approval on-request --cd <absolut-testrepo>` | Exit 1, `agent_not_ready`: agenten blocked under startup. Namnet/panen finns kvar; ingen andra agent startades. |
| Återläs agent | `agent get hc-f10-codex` | Codex i rätt cwd, samma pane/terminal, `launch_pending=true`, `agent_status=blocked`, revision/state_change_seq 1. Ingen beständig Codex-sessionidentitet rapporterades ännu. |
| Läs blockerare | `agent read hc-f10-codex --source visible --lines 40` | Verklig Codex-dialog “Trust this folder?” för just testrepon. |
| Läs historik vid blockerare | `agent read hc-f10-codex --source recent-unwrapped --lines 50` | Exit 1, `agent_not_idle`. Alternativskärmshistorik kan kräva scrollning vid idle; använd visible vid blocked. |
| Prompt till blocked agent | `agent prompt hc-f10-codex 'F10 interface probe: reply exactly HC_F10_OK; do not run tools or change files.' --wait --timeout 15000` | Exit 1, `agent_blocked`; ingen input skickad. Detta är ingen lyckad promptleverans. |
| Bekräfta process/cwd | `pane process-info --pane w1:p1` | Shell och foreground Codex-processer hittade; argv innehåller read-only/on-request och rätt absolut cwd. Dessa fakta bekräftar process, inte färdig start eller uppdragsacceptans. |
| Saknad agent | `agent get hc-f10-absent` | Exit 1, `agent_not_found`; inget implicit nyagentsskapande. |

### Väntande verkliga prov

Användarbeslut om testrepots trust-dialog är begärt. Herdrs inbyggda skill kräver användarinput före svar på approval-/frågedialoger och tillåter inte `--trust-repository` som retrylösning. Dialogen har lämnats obesvarad. Nästa ansvariga roll: operatören beslutar, därefter Integration/Worker fortsätter samma registrerade pane.

Följande är **inte verifierat**: lyckad interaktiv readiness, godkänd prompt och arbetsstart, beständigt Codex-session-ID, återanslutning efter transport-/processomstart, riktat stopp samt parkering och resume. Inget av detta får användas som bevis för ledig workerslot eller task Done. F-11 och senare beroende implementation väntar på avslutad F-10.

## Installerad Herdr-semantik (hjälp/skill/schema; ej livebevis)

`agent start` väntar på att rätt agentkind upptäckts i samma terminal och är redo för input. `agent_not_ready` behåller namnet för avstämning. Upprepa inte start innan get/read/process-info visar utfallet.

`agent prompt --wait` kräver observerad working eller blocked inom 5 sekunder när sändning börjar i annan icke-working status. Standardmålet är idle/done/blocked. Kommandot följer inte turn-ID: om agenten redan arbetar kan den gamla turnens slut matcha. Produktadaptern behöver egen korrelation och domänbekräftelse; levererad text och Herdr done betyder inte taskacceptans/READY_FOR_REVIEW.

Herdr-statusar är idle/working/blocked/done/unknown. Unknown är varken klar, stoppad eller parkerad. `pane report-agent-session` kan rapportera ID/path och resume-argv; rapporterat värde måste jämföras med faktisk runtime innan det blir ownership- eller sessionsbevis. Det får inte fabriceras för att fylla ett saknat fält.

`agent send-keys` kan skicka esc/ctrl+c, `agent attach` öppnar terminalanslutning och `session stop <name>` stoppar hela namngivna Herdr-sessionen. Dessa är inte ännu verifierade som produktens riktade park/stopp. Parkering kräver bevis för upphörd aktivitet och bevarad återupptagbar Codex-session, inte bara terminalstatus eller signalens returkod.

## Codex app-server och installerat schema

`codex app-server --listen stdio://` startades som en separat testchild för lokala sandboxkommandon. JSON per rad användes på stdin/stdout. `initialize` med clientInfo/capabilities följt av `initialized` lyckades. Ingen thread/turn eller modellförfrågan skapades i detta sandboxprov. Childprocessen avslutades efter proverna.

`permissionProfile/list` returnerade `:read-only`, `:workspace`, `:danger-full-access`, alla allowed. Allowed är konfigurationskapabilitet, inte användarens tillstånd att använda farlig profil; den senare användes inte. Ny CLI kräver `codex sandbox --permission-profile <NAME>`; äldre försök med bara sandbox_mode-konfiguration avvisades med exit 2 innan något kommando kördes.

Installerat genererat schema anger `thread/start`, `thread/resume(threadId)`, `turn/start(threadId,input)` och `turn/interrupt(threadId,turnId)`. Den [officiella app-serverdokumentationen](https://learn.chatgpt.com/docs/app-server) beskriver handshake, beständig thread/resume, turnnotifikationer och interrupted-resultat. Det är ett möjligt strukturerat gränssnitt; live thread/resume/interrupt har ännu inte provats och är inte valt som ersättning för obekräftad Herdr-runtime.

Schemas framställdes med `herdr api schema --output <fil>` och `codex app-server generate-json-schema --out <dir>`. SHA256 för Herdr-schemat: `9e2af207e9aa8183d4aeca5fde9cc48e7909bb40cdbd7cf21608a6d3ea78075b`; Codex v2-schema: `62f227e897351f20fe8b8984341512d1cc4ebf42def4487bc65d36fb7c40bba6`. Regenerera vid versionsbyte; API-ytan ska inte antas stabil över uppgraderingar.

## Verkliga sandboxprov

Via app-server `command/exec` kördes en Python-process med explicit cwd, sandboxPolicy, 15000 ms timeout och 2000 bytes outputcap. Fil- och nätprover använde bara ofarliga filer och lokal loopback-bindning. Båda anrop returnerade exitCode 0 med följande observerade resultat:

| Kontroll | readOnly, networkAccess=false | workspaceWrite med exakt testrepo, networkAccess=false, excludeSlashTmp/excludeTmpdirEnvVar=true |
| --- | --- | --- |
| Skriva i cwd | OSError errno 30 (read-only filesystem) | Tillåtet |
| Skriva i testrepots föräldrakatalog | OSError errno 30 | OSError errno 30 |
| Läsa ofarlig fil i föräldrakatalog | Tillåtet | Tillåtet |
| Binda lokal nätverkssocket | PermissionError errno 1 | PermissionError errno 1 |

Proven styrker dessa kommandoprocessers filskrivnings-/nätgränser, inte att all läsning utanför tasken spärras eller att MCP, plugin, ärvda credentials och alla runtimeverktyg isoleras på samma sätt. Read-only och workspace-write skyddar inte i sig read-secrets. Gitmetadata kan dessutom delas mellan riktiga worktrees; den särskilda gränsen måste provas i F-17. Betrodd MCP-rollprofil måste ligga utanför Workerns skrivområde. TeamPlayer- och Gitservicepolicy behövs även när sandboxen fungerar. Autonom merge får inte aktiveras enbart med dessa prov.

## Återstående acceptans

- F-10.A1: delvis verifierad; lyckad prompt, resume, stopp/park återstår enligt ovan.
- F-10.A2: inte verifierad; beständig Codex-session och faktisk återanslutning återstår.
- F-10.A3: verifierade sandboxresultat och startupblockerare finns; parkering är fortfarande uttryckligen obekräftad.

F-10 är inte READY_FOR_REVIEW eller Done. Spara nästa prov på samma resurs, dokumentera användarbeslut och kör återstående livscykelprov innan review/integration.
