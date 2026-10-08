# Verifiering av Herdr/Codex-gränssnitt — F-10

Datum: 2026-10-08. Status: **prov avslutade — redo för Integration-review**. Detta är ett underlag för F-11–F-13, inte ett påstående om färdig runtimeintegration.

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

### Genomförda prov efter användarbeslut

Användaren godkände uttryckligen trust för exakt testrepo. `agent send-keys hc-f10-codex enter` besvarade den återlästa trust-dialogen i samma pane. Därefter rapporterades `interactive_ready=true`, idle och rätt cwd. Kontots frivilliga security-setup-banner stängdes med esc; ingen säkerhetsinställning ändrades.

| Operation | Faktiskt prov och resultat |
| --- | --- |
| Prompt/startobservation | Korrelationsprompt HC_F10_TURN_1 med `--wait --timeout 45000` gav `agent_prompted`, idle efter activity gate, state_change_seq 6 och exakt `HC_F10_TURN_1_OK` i agent read. |
| Beständig originalsession | Codex `/status` i rätt pane visade `01a11ab3-c066-7260-9c11-762acfcfaa37`; app-server thread/read bekräftade samma ID/sessionId/cwd och testmarkören i lagrad historik. |
| TUI-stopp och återupptagning | ctrl+d följt av avstämning gav agent_not_found och enbart bash som foreground. `agent start ... -- resume <original-ID> --sandbox read-only --ask-for-approval on-request --cd <testrepo>` återupptog samma session. Nästa prompt fick exakt `HC_F10_TURN_1_OK HC_F10_TURN_2_OK`, alltså bibehållen historik. |
| Delad daemon-begränsning | Efter TUI-stopp av originalsession gav en separat app-server thread/resume fel -32600 “already has an active writer”. Lokal thread/read visade notLoaded trots annan servers writer. `codex app-server proxy` initialize nådde inte svar inom 40 s; anslutningen stängdes utan runtimeändring. Dessa vägar väljs inte för produktens stoppbevis. Global Codex-daemon stoppades inte. |
| Isolerad runtime | En andra, uttryckligt avgränsad kapabilitetssession startades i samma testpane efter original-TUI-stopp: `agent start hc-f10-private --kind codex --pane w1:p1 --timeout 30000 -- --no-daemon --sandbox read-only --ask-for-approval on-request --cd <testrepo>`. Readiness, cwd och argv bekräftades. Detta är ett separat dokumenterat prov, ingen ersättning av en produkt-run. |
| Native sessionsignal | Efter första korrelationsprompten rapporterade Herdr `agent_session={agent:codex,kind:id,source:herdr:codex,value:01a11ab7-a01c-7e70-8203-e7a26abd3526}`. Svar `HC_F10_PRIVATE_1_OK`. Ingen sessionsrapport injicerades manuellt. |
| Park/stopp av isolerad runtime | ctrl+d gav först fortfarande agent/process — signalack är asynkront. Vid återläsning var agentnamnet borta, endast shell foreground och alla fem fångade processidentiteter (PID + Linux startTime) borta, inklusive barn. Herdr workspace/pane och repo behölls. |
| Strukturerad resume | Efter bekräftat stopp lyckades separat stdio app-server thread/read och thread/resume med exakt private-ID, cwd och read-only/on-request. Samma ID/sessionId, idle, bevarad markör och readOnly/networkAccess=false. Probechild stängdes därefter. |
| Herdr-resume av parkerad session | `agent start hc-f10-private ... -- resume <private-ID> --no-daemon --sandbox read-only --ask-for-approval on-request --cd <testrepo>` gav agent_started/interactive_ready. Prompt gav exakt `HC_F10_PRIVATE_1_OK HC_F10_PRIVATE_2_OK`; Herdr rapporterade samma native session-ID. |
| Avbryt aktiv turn | En ofarlig lång textsvarsprompt med `--wait --until working --timeout 20000` gav working seq 23. esc följt av `agent wait ... --until idle --timeout 30000` gav idle seq 24 och synlig “Conversation interrupted”. Därefter ctrl+d och avstämning till agent_not_found/shell. Persisted thread/read visar tre turns: completed, completed, interrupted. |
| Upprepat stopp | Ny `agent send-keys hc-f10-private ctrl+d` efter bekräftat stopp avvisades agent_not_found; inget skickades till shell eller annan agent. En adapter kan tolka detta som redan stoppad bara med sparat ownership och separat inaktivitetsbevis. |
| Avslut av testserver | Efter stopp/read/resume-proven stängdes probechild. `herdr --session hc-f10-20261008 server stop` och efterföljande status gav not running för exakt denna namngivna socket. Repor/historik finns kvar; ingen global server eller användarworkspace stoppades. |

### Valt kontrakt för F-11–F-13

Använd explicit Herdr-serveridentitet, registrerad workspace/pane/terminal, unik agentidentitet och `codex --no-daemon` med operatörens valda sandbox/approval-policy. Start räknas endast med interactive_ready, rätt kind/cwd/pane/terminal och matching process-argv. Ett nytt Codex-session-ID kan saknas före första turn; lagra tillgängliga ID:n direkt och fånga native agent_session när den blir tillgänglig. Krävs session-ID innan uppdrag ska avsaknad ge ett uttryckligt vänteläge, inte ett fabricerat ID.

Parkering här är **stopp av den ägda isolerade runtimeprocessen med bevarad beständig konversation**, följt av explicit `codex resume <exakt-ID> --no-daemon` i samma ägda worktree. Det är inte OS-suspend. Avbryt aktiv turn först, bekräfta upphörd aktivitet, stoppa TUI och verifiera ägda processidentiteter inklusive barn. Först därefter kan slot frigöras. Bevarad konversation verifieras med thread/read och vid återstart med samma native ID och cwd. Återanslutning till redan levande runtime använder get/read, aldrig ytterligare start.

Idempotens, scopekontroll, processidentitet, operation journal och slots måste implementeras i F-11–F-13/efterföljande services. CLI ensam erbjuder inte runownership eller idempotens. En saknad/förändrad pane eller osäkra processfakta kräver avstämning och får inte leda till breda kill-kommandon, ny implicit session eller slot-release. Återstart av Herdr-server, faktisk approval-blockerare under aktiv turn, verktygsbarn som inte avslutas, helautomatisk Worker-policy och begränsade läsrötter verifieras senare; de påstås inte bevisade här.

## Installerad Herdr-semantik (hjälp/skill/schema; ej livebevis)

`agent start` väntar på att rätt agentkind upptäckts i samma terminal och är redo för input. `agent_not_ready` behåller namnet för avstämning. Upprepa inte start innan get/read/process-info visar utfallet.

`agent prompt --wait` kräver observerad working eller blocked inom 5 sekunder när sändning börjar i annan icke-working status. Standardmålet är idle/done/blocked. Kommandot följer inte turn-ID: om agenten redan arbetar kan den gamla turnens slut matcha. Produktadaptern behöver egen korrelation och domänbekräftelse; levererad text och Herdr done betyder inte taskacceptans/READY_FOR_REVIEW.

Herdr-statusar är idle/working/blocked/done/unknown. Unknown är varken klar, stoppad eller parkerad. `pane report-agent-session` kan rapportera ID/path och resume-argv; rapporterat värde måste jämföras med faktisk runtime innan det blir ownership- eller sessionsbevis. Det får inte fabriceras för att fylla ett saknat fält.

`agent send-keys` kan skicka esc/ctrl+c, `agent attach` öppnar terminalanslutning och `session stop <name>` stoppar hela namngivna Herdr-sessionen. esc/ctrl+d och explicit testserverstopp är verifierade ovan; terminalattach och generellt ctrl+c-stopp är endast inventerade. Parkering kräver bevis för upphörd aktivitet och bevarad återupptagbar Codex-session, inte bara terminalstatus eller signalens returkod.

## Codex app-server och installerat schema

`codex app-server --listen stdio://` startades som en separat testchild för lokala sandboxkommandon. JSON per rad användes på stdin/stdout. `initialize` med clientInfo/capabilities följt av `initialized` lyckades. Ingen thread/turn eller modellförfrågan skapades i detta sandboxprov. Childprocessen avslutades efter proverna.

`permissionProfile/list` returnerade `:read-only`, `:workspace`, `:danger-full-access`, alla allowed. Allowed är konfigurationskapabilitet, inte användarens tillstånd att använda farlig profil; den senare användes inte. Ny CLI kräver `codex sandbox --permission-profile <NAME>`; äldre försök med bara sandbox_mode-konfiguration avvisades med exit 2 innan något kommando kördes.

Installerat genererat schema anger `thread/start`, `thread/resume(threadId)`, `turn/start(threadId,input)` och `turn/interrupt(threadId,turnId)`. Den [officiella app-serverdokumentationen](https://learn.chatgpt.com/docs/app-server) beskriver handshake, beständig thread/resume, turnnotifikationer och interrupted-resultat. thread/list med exakt cwd-filter, thread/read och thread/resume är nu verifierade för den stoppade isolerade sessionsvarianten. thread/start och turn/interrupt är endast inventerade; verklig avbrytning provades med Herdr esc och lagrad interrupted-status. App-server startar inte en ersättningssession på resume-fel.

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

## Taskacceptans och underlag

- F-10.A1: verifierade anrop/resultat för workspace/pane, start, prompt, status, återanslutning och stopp; konkreta begränsningar för delad daemon/proxy och uppgraderingar dokumenterade.
- F-10.A2: båda nya testkonversationerna identifierades beständigt; faktisk Herdr-resume bevarade respektive ID/cwd/historik. Isolerad private-session verifierades dessutom med native Herdr-session-ID och strukturerad resume efter processstopp.
- F-10.A3: park genom isolerat processstopp/resume, asynkron start/stopp och observerade sandboxgränser skiljs från inventerade/obekräftade funktioner.

Sanerat maskinläsbart [provunderlag](F-10-prover.json). Detta verifierar gränssnitt och kapabiliteter; produktens runtimeadapter och ownership/recovery implementeras i F-11–F-13. E-03:s samlade acceptans är inte automatiskt uppfylld av manuella CLI-prov.
