# F35: Integration styr tasks genom registrerade verktyg

## Betrodd komposition

Operatören ansluter `IntegrationControlService(epic_start, scheduler, log)` till en
F34-startservice och F29-scheduler med samma Settings, SQLite, projekt och fulla
operatorvalda LocalTaskSpec. `review_provider` måste vara `None`: den registrerade
agenten begär själv review, korrigering och godkännande med aktuellt underlag.
`control.runtime(log)` skapar den begränsade anslutningen. Den kan genomföra MCP-
handshake före start-ACK, men verktygsanrop kräver faktisk F34-registrering med
rätt process, session, pane, uppdrag och profil. Använd inte en allmän RuntimeService
som genväg för denna agent. CLI startar ingen implicit komposition eller loop.

Operatören startar hosten utanför agentens tillitsgräns och öppnar TeamPlayer med
F24/F25:s uttryckliga endpoint/headerprovider. Credentials, profiler och SQLite
är operatorresurser och ingår inte i agentens verktygsargument eller uppdrag.
F04-profile ska vara owner-only, utanför Git och alla worktrees. Samma OS-användare
är inte en fullständig isoleringsgräns; F38:s driftsgate återstår.

```python
launch = CodexMCPLaunch(command=absolute_python, args=(protected_host, protected_config))
integration_adapter = HerdrAdapter(server, sandbox="read-only", mcp=launch)
worker_adapter = HerdrAdapter(server, sandbox="workspace-write")
# Hostens explicita komposition, med redan valda scope, tjänster och transport:
control = IntegrationControlService(epic_start, scheduler, log)
await serve_stdio(control.runtime(log))
```

Codex får en lokal stdio-konfiguration genom separata `-c`-argument, utan shell-
interpolation eller ändring av global Codex-konfiguration. Konfigurationen anger
hostens executable/argv, 120 sekunders start-/tool-timeout och den fasta ofarliga
markören HERDR_ENV=1 för MCP-processen. Inga credentialvärden överförs där.
Start/livscykeljournalens fingerprint binds till hela faktiska konfigurationsargv;
ändrad host/config kräver avstämning och nekas som samma startavsikt. Worker-
adaptern får ingen Integration-konfiguration. MCP-schema har inga launch-, roll-,
profil-, databas- eller transportargument.

## Agentens arbetsgång

1. Läs `integration_overview` och `task_get_next` för egen registrerad epicrun.
   Kandidater har aktuella blockerarkoder. Overview anger faktisk Gitcommit,
   taskstate, Worker-reservation, native runtime och nästa begäran. Dessa är
   vägledning: tjänsten validerar alltid villkor igen vid nästa anrop.
2. Välj exakt en konfigurerad task via `task_start`. F29:s gemensamma lås och F15
   kontrollerar färsk User-tilldelning, epicstatus, dependencies, Git och unik slot
   före native start. Alternativt utför `task_schedule` en begränsad F29-tick som
   kan fylla ledig kapacitet i ordning. Integration förbrukar ingen Worker-plats.
3. `task_schedule` samlar native rapport och oberoende tester. REVIEWING-taskens
   overview innehåller `request_key` och `context_id`. Hämta hela paketet med
   `task_review_request` och samma request_key; läs aktuell full diff, kontrakt,
   acceptans och testbevis. Ett nytt nyckelvärde är inte en ersättning för review.
4. Begär `task_request_changes` med konkreta numrerade issues/kriterier eller
   `task_approve` med komplett aktuell beslutstext. Feedback återgår till samma
   Worker/session/branch/worktree. För återförsök behåll request_key och beslut.
   Ändrad task/epiccommit kräver ny verifiering och review. `task_merge` utför
   aktuell godkänd Task → Epic, eftertest och fysiskt stopp genom F21.
5. `task_block_review` respektive `task_park_blocked` sparar verkligt hinder och
   frigör slot först efter verifierad inaktivitet. `resume_task`/`worker_resume`
   kräver explicit InputDecision med blocker_id, input_id och konkret svar; ingen
   defaultinput eller ny Worker-session. F31/F32 äger park/resume-bevisen.

Varje operation ger säkert faktiskt resultat plus ny overview. Om overview saknas
behålls redan känd sidoeffekt, status UNAVAILABLE och inga föreslagna operationer.
Om boardmirror misslyckas anger resultatet board_sync=PENDING. Om en efterföljande
controller-checkpoint misslyckas behålls också det faktiska serviceresultatet med
control_refresh=PENDING; återläs och återförsök samma sparade begäran, utan ny dispatch. Återläs journalen
eller använd taskens `set_task_status`; replaya inte merge/start för att laga text.
Taskverktygen får inte välja status eller fabricera verifiering. Färsk board ägare,
aktuell registered Integration och samma schedulerlås krävs även vid review,
merge och resume. Otillåtna rapport-/epic-/mainmutationer är inte exponerade.

## Verifieringsgränser

Kontrollerade MCP-SDK-prov använder riktiga temporära Git-repositories, SQLite,
fulla contexts och oberoende testprocesser, med simulerad board/runtime. Native
fixture använder nya riktiga User105-tasks och registrerade F05/F34-resurser i det
redan godkända ofarliga repot. Agentens faktiska MCP-anrop måste läsas ur Codex-
tråden och jämföras med Git, operationer, slots, native sessioner och TeamPlayer.
F36 levererar samlad epicverifiering/överlämning; F37 verifierar det fulla scenariot.
Fixtureepic förblir Active utan main-merge. Historiska prov binds till deras egna
sourcecommits; senare kodändringar gör inte gamla filhashar till aktuella bevis.

## Pågående nativeprov 2026-10-09

[Provstatus](F-35-progress.json) beskriver aktuell verifieringsgräns. Första
runen registrerades men hosten saknade HERDR_ENV och inga Workers startade.
F13-stoppet bekräftades innan bara dess namngivna server stoppades. Resurser och
journal sparades. R2 har egen ny run, SQLite, branch och User105-fixtureepic.
Native Integration har faktiskt anropat integration_overview/task_get_next via
stdio. R2-taskernas namn rättades före start så de exakt motsvarar registrerad
LocalTaskSpec; beroendekontrollen och övrigt scope ändrades inte.

GitKraken-hooken PermissionRequest i operatorns ~/.codex/hooks.json väntade på
ett externt behörighetsbeslut för herdr_coordinator.task_start. Native-tråden
registrerar sedan `user cancelled MCP tool call`; anropet kom inte till hosten.
Färsk journal visar noll tasks/Workerstarter/merger. Det finns ingen startmutation
att återköra för avstämning. Upprepa inte en avbruten klientbegäran automatiskt.

## Återupptaget nativeprov 2026-10-10

Användaren begär fortsatt prov och bekräftar att GitKraken kör på Pi:n. F13
avvisade r2-resume före effekt med RUNTIME_PANE_CHANGED: serveromstarten
skapade en ny terminalidentitet. Den ursprungliga stoppade sessionens journal,
refs, worktrees och SQLite bevaras; bindningen ändras inte och ingen runtime
adopteras. Ingen resume-operation eller Worker skapades i det nekade försöket.

Ett nytt separat r3-prov använder samma godkända repo och samma fortfarande
Pending-fixturetasks under samma externa epic. F05/F34 har registrerat run
eaac9f71-c484-42e2-8af6-2324a9ea9198/session
01a125ed-dc9c-78a2-9f3a-ab8cb507fcc7 i ny SQLite, branch och worktree.
Den egna servern hc-f35-r2-20261009 används efter verifiering av tom agentlista.
Ny skyddad operatorprofil/host ligger utanför agentens worktree och innehåller
inga credentialvärden. Exakt ett nytt explicit kontrolluppdrag har skickats;
Integration har faktiskt anropat integration_overview och task_get_next.

GitKraken-hookens verkliga behörighetsbeslut hanteras av användaren. Hosten
kringgår inte hooken. Full nativeacceptans, review, utvecklingstaskmerge och
Done återstår. F35 NeedsApproval/Attention9; E09 Active8. Nästa ansvariga roll är
Integration-operatören för observation och verifiering av faktiskt taskflöde.

## Lokala kontroller

`uv run --locked pytest tests/test_epic_start.py tests/test_runtime_start.py
tests/test_runtime_lifecycle.py tests/test_task_scheduler.py tests/test_mcp.py
tests/test_codex_mcp_launch.py tests/test_integration_control.py -x` passerade
134 tester/exit 0 på 435,46 sekunder. Efter en ytterligare controller-korrigering
passerade `uv run --locked pytest tests/test_integration_control.py
tests/test_codex_mcp_launch.py -x` med 19 tester/exit 0 på 336,54 sekunder
mot den slutliga koden. Dessa prov använder verklig Git/SQLite/testprocess men simulerad
runtime/board och ersätter inte det återstående native taskflödet.

Ett tidigare positivt fixtureprov använde en sekunds start-ACK-deadline och
missade denna under samtidiga verifieringar. F35-fixturen använder nu samma
45 sekunders deadline som produkten, utan ändring av produktens policy eller
återförsöksregler. Testets session/rapportfil-lista och förväntad review-resume-
fas rättades också mot faktiskt kontrakt. Separata deadline-/stale-/ownership-
prov finns kvar; endast passerade kontroller räknas som verifiering.

Ruff, README:s CLI-check, build, exakt wheelinnehåll (60 moduler/tre policies),
97 lokala dokumentlänkar, diff-whitespace och faktisk credentialvärdesskanning
passerade. uv.lock och SQLite-schema är oförändrade. F34:s historiska native-
filhashar jämförs med dess sourcecommit e8b2dc2, inte F35:s ändrade filer.

Aktuell GitKraken PermissionRequest för task_start väntar i registrerad r3-session.
Session/server behålls igång för användarens beslut på Pi:n; inga Workers
eller merger finns ännu. Begäran upprepas inte och hooken kringgås inte.
