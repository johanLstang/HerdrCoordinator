# F-13: ägd återanslutning, stopp och samma-session-resume

`RuntimeLifecycleService(settings, store, herdr, codex=None, processes=None)` har separata interna `reconnect_task/epic`, `stop_task/epic(key)` och `resume_task/epic(key)`. Integration styr Tasks inom sitt projekt/epic; Coordinator styr Epic-runtime. Registrerad F-05 Gitägare/branch/worktree och lyckad F-11-start binds till explicit server, sandbox, agentnamn, pane/terminal och verifierad faktisk process. MCP är fortsatt read-only.

## Återanslutning

En levande matchande runtime observeras utan extra start och ger LIVE med verifierat sessions-ID och fysisk status. Taskslot måste fortfarande vara reserverad. En sparad konversation läses via privat Codex app-server thread/read med exakt id/sessionId/cwd. Vid resume kan native Herdr-hook ännu saknas: faktiskt verifierat installerat Codex-processargv med `resume <exakt UUID>` och matching backendmetadata styrker då identiteten. En avvikande hook/argv/session avvisas.

STOPPED kräver sparat lyckat stopp för aktuell generation plus ny fysisk inaktivitetskontroll. Saknad agent utan sådant bevis ger RUNTIME_MISSING_UNCONFIRMED; inget implicit ersättningsworktree, ny konversation eller slotrelease sker. Tappad adapteranslutning är inte bevis för förlorad/inaktiv session. Saknad beständig konversation ger fel före återstart; globala Codex-/Herdr-servrar stoppas inte.

## Stopp och parkering

Unik operation `(project, stop_runtime, key)` lagrar generation, orsak och processbevis före input. En aktiv Task övergår först till BLOCKED med konkret orsak och bevarad resumefas; task-Done ändras inte. Startjournal, Code-session, branch och worktree behålls. Ny nyckel efter redan verifierat stopp får en beständig aliasoperation för samma generation, så en framtida retry inte stoppar en återupptagen runtime.

Linux-adaptern tar PID/startTime för de verifierade Codex-rootprocesserna, deras observerade barn och processgrupper. Den utvidgar till barnens grupper och vägrar scope som omfattar shellens processgrupp. Snapshot tas före interrupt och igen före exit, och förenas beständigt. Processer som bytt PID-identitet räknas separat; grupp som fortfarande har medlemmar hindrar bekräftelse även vid reparenting.

Endast working/idle/done är stoppbara. Blocked/unknown får ingen input: operatören måste hantera dialog eller avvikelse. Vid working skickas esc till exakt ägt namn och idle väntas begränsat. Persisted inProgress-turn måste ha upphört före exit. ctrl+d skickas till samma validerade agentnamn; CLI-svar är inte stoppbevis.

Bekräftelse kräver samtliga följande:

- samma ägda pane/terminal/worktree,
- agentnamnet saknas,
- enbart den kända shellprocessen finns i foreground,
- sparade PID/startTime-identiteter är borta och sparade grupper har inga medlemmar.

Först därefter skrivs STOPPED/SUCCEEDED och inactivity=true. BLOCKED Task blir PARKED och slotten frigörs atomiskt. Epicstatus ändras inte. Efter okänt signalutfall återläses faktiska resurser; bekräftat stopp får ingen ny signal. Kvarvarande barn/processgrupp, ändrad identitet, scan-/anslutningsfel eller ofullständigt stopp ger konkret fel och behåller slotclaim. Ingen PID-kill, bred processkill eller cleanup används.

## Resume

Ny resume-operation kräver bekräftat stopp för aktuell generation, kvarvarande sessionmetadata, rätt scope/Gitägare och aktiv Epic. Tasken ska vara PARKED. Slot räknas/reserveras inom SQLite-transaktion före extern start; högst två claims i projektet. Intent sparar stop-operation och exakt sessions-ID. En annan pending stopp-/resume-operation för samma run blockerar ny operation.

Herdr startar samma namn i samma pane med `codex resume UUID --no-daemon --sandbox <valt läge> --ask-for-approval on-request --cd <samma worktree>`. Readiness, faktiskt argv/cwd/PID/startTime och samma beständiga konversation verifieras. Tasken återgår genom StateService till bevarad resumefas; aktuella processer/generation uppdateras och resume-operationen behåller underlaget. Inga branch/worktree-/sessionsersättningar skapas.

Okänt resumeutfall eller processavbrott efter intent observeras endast; saknad agent leder till RESUME_OUTCOME_UNKNOWN och reserverad slot behålls. Samma operationsnyckel återanvänder verifierad runtime. Nyckel från äldre generation får inte styra nya processer. Besluts-/fixprompt, full Attention-policy och automatisk scheduling tillhör F-31/F-32 och senare features.

## Tidsgränser och begränsningar

CLI-anrop är begränsade; interrupt väntar högst 30 s, resume-start 30 s och fysisk stopavstämning pollas högst 3 s efter input. En deadline frigör aldrig en slot utan bevis. Recovery av misslyckad F-11-start utan READY-processbevis, Herdr-serveromstart och godtyckliga verktygsbarn som redan lämnat både observerat processträd och processgrupper kräver senare runtimepolicy/recovery. Linux-snapshot är ingen cgroup-isolering; F-17 måste verifiera full Worker-policy innan autonom implementation och merge. Oavslutade observerade barn hålls kvar som hinder.

## Verifiering

Kontrollerade lifecycleprov använder verkliga Git-worktrees och SQLite, med simulerad runtime separat från livebevis. F-13:s verkliga prov kör F-05 worktreecreation, F-11 start, F-12 prompt/native ACK, reconnect, stopp/park/slotrelease, SQLite-reopen, resume av samma session och andra generationens upprepade stopp genom produktservices. Varje stopp fångade fem processidentiteter och tre grupper. Ofarlig prompt använder inga tools, kodändringar eller delegation. Se [sanerade prov](F-13-prover.json).
