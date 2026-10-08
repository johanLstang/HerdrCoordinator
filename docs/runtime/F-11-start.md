# F-11 — beständig start av ägd runtime

`RuntimeStartService.start_task(actor, task_run_id)` kräver den registrerade epicens Integration-principal, en aktiv EpicRun och en CLAIMED TaskRun med verifierad F-05-worktreeägare/branch. `start_epic(actor, epic_run_id)` kräver Coordinator och en aktiv, ägd epic. Dessa är interna serviceanrop; F-04:s MCP-server är fortfarande endast read-only. Produktens task-startverktyg och verifiering av verkliga TeamPlayer-beroenden levereras i senare tasks.

## Startup och kapacitet

Worker-slot (1–settings.max_workers, högst 2) reserveras under SQLite BEGIN IMMEDIATE. Alla andra taskclaims i samma projekt räknas, även kvarvarande claims från stoppade eller avslutade runs. Slot, runtime-agentnamn, serveridentitet, operation och STARTING-event skrivs atomiskt före externa sidoeffekter. Worker-slot släpps aldrig av startservicen. Epicens integrationsruntime använder ingen Worker-slot.

Herdr-session anges av operatören i `HerdrAdapter(server_session, sandbox="read-only")`; installerad Herdr-kontext krävs. Bara read-only/workspace-write kan väljas. Adaptern startar Codex med --no-daemon och on-request; inga farliga bypassflaggor eller produktuppdrag skickas. Operatorn ansvarar för att Herdr-servern och Codex-inloggningen redan fungerar. Trust-dialoger besvaras inte av servicen.

## Journal och recovery

| Operationsfas | Beständigt underlag | Nästa säkra steg |
| --- | --- | --- |
| INTENT | Run/scope/branch/cwd/server/sandbox, unikt agentnamn/label, slot och STARTING | Skriv nästa intent och skapa workspace. |
| WORKSPACE_CREATE_REQUESTED | Avsikt committad före API-anrop | Om svar/ack saknas: avstämning. Skapa inte ytterligare workspace och adoptera inte en labelmatchning som bevis. |
| WORKSPACE_CREATED | Workspace/tab/pane/terminal i run, operation och externa referenser | Kontrollera samma pane/cwd och idle shell; omvalidera Git/roll/slot före start. Återanvänd sparad workspace. |
| AGENT_START_REQUESTED | Intent committad före CLI-start | Läs endast känd agent/pane. Bekräfta cwd/kind/terminal/process och readiness. Ingen blind ny start om agenten saknas eller utfallet är okänt. |
| READY, SUCCEEDED | Herdr-bindning och actual foreground PID + Linux startTime | Upprepad start återläser/validerar samma resurser och processidentiteter. Förändrad/saknad runtime kräver avstämning. |

Kända fel lagras som sanerad `error_code` på operationen. Kända resurser och slot behålls vid blockerare och partiella fel. Krasch innan/efter en okänd extern skrivning kan kräva operatörsbeslut; F-11 ger ingen generell automatisk rekonstruktion. SQLite skyddar concurrent journal-/slotförändringar; externa manuella Git-/Herdrändringar omfattas inte av ett distribuerat lås. Git valideras åter före agentstart och vid efterföljande observation.

CLI:s exit 0/resultat läses från stdout; fel-JSON på stderr kontrolleras mot kända felkoder utan råloggning. Startup kräver interactive_ready=true, rätt Codex/cwd/name/pane/terminal, installerad Codex-launcher/native binär och förväntade --no-daemon/sandbox/approval/cwd-argument, jämförelse med verklig /proc cmdline/cwd och Linux process startTime. Ett tillgängligt native sessions-ID verifieras även via privat Codex app-server thread/read för exakt ID/cwd. Läsningen startar ingen konversation eller turn.

STARTING innebär ingen domänbekräftelse på uppdrag. Tasken övergår inte till WORKING i F-11. Ny Codex-konversation kan sakna ID före första turn; null är explicit, medan alla då tillgängliga Herdr-ID:n sparas. F-12 ansvarar för korrelerat uppdrag/startbekräftelse och sessionssignal. F-13 ansvarar för bekräftat park/stopp/resume och slotrelease.

## Persistens

EpicRun och TaskRun får nullable `herdr_server_session`, `herdr_tab_id`, `herdr_pane_id`, `herdr_terminal_id` utöver tidigare workspace/agent/Codex-ID. Befintliga JSON-payloads får default null vid inläsning; tabeller och schema 2 är oförändrade. `update_runtime_metadata` får ändra endast runtime-/slotfält och inte status, branch, worktree eller Gitcommits. Stateövergångar använder fortfarande StateService/transition_events.

Operation är unik per (project_id,start_runtime,run_id). Externa Herdrreferenser har provider `herdr:<server-session>` så att lokala w1-ID:n i skilda servrar inte krockar; befintliga relationer och owner-index skyddar resurser i samma projekt. Operatören får inte återanvända servernamn och nya terminaler som gamla ownershipbevis. Run/operation är också bundna till faktisk F-05-creation-operation och Gitbranchägare.

## Verifiering

Kontrollerade CLI-fakta testas med verkliga temporära Git-repos/worktrees och SQLite: start/reopen/dubbelstart, samtidiga anrop för samma run, max två reserverade slots, scope/roll, workspace-/startavbrott, trust-blockerare, fel cwd/branch och branchändring efter workspace-skapande, metadatarestriktion och CLI-kanaler/timeout. FakeHerdr är uttryckligen en adapterfixture.

Det [verkliga startprovet](F-11-prover.json) använder en separat Herdr-server, äkta F-05-registrerade test-runs och worktrees ur användarens godkända Git-testrepo. Task- och epicstart/repeat/reopen ger exakt samma resurs- och processidentiteter. Ingen produktprompt skickades, ingen TeamPlayer-integration påstås och setupens principals/tomma beroenden styrdes manuellt. Tidigt CLI-stderr-fel bevarade workspace/slot och gav ingen falsk readiness; retry efter adapterkorrigering återanvände samma workspace. Efter provet stoppades testagent/server manuellt av bootstrap Integration; runhistoria/slot finns kvar för avstämning.

Fullständig rollisolering av shell, credentials, hooks/plugins och delad Gitmetadata är inte verifierad av startup. Det återstår i F-17 och krävs före autonom Worker-policy/main-merge enligt D-02. Detta är ett startkontrakt, inte en garanti om full OS-isolering.
