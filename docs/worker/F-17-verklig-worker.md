# F-17: verklig Worker-MVP och runtimegränser

Prov 2026-10-08 med Herdr 0.9.3/protokoll 22 och Codex 0.161.0. [Maskinläsbart underlag](F-17-prover.json) innehåller native identiteter, ACK, rapport, oberoende testoperation, Git-SHA, MCP-beslut och stoppbevis. Testrepositoryt är det uttryckligen godkända, ofarliga F-10-repositoryt. Inga credentials lästes eller publicerades.

## Verklig leverans

Coordinator skapade en separat F-05-testepic med fixture-AGENTS och aktiverade den. Integration lämnade en explicit version-1-task till F-15: implementera `normalize_label`, Unicode/whitespace/feltester och README med standard-library unittest. Specifikationen avgränsade Worker till egen task-branch/worktree.

Godkänd provkörning: projekt `f17-probe`, epic-run `f17-epic-run`, task `f17-task-sixth-attempt`, run `25bf44fa-2aff-45d0-b57d-e732fc722dce`. Herdr-server `hc-f17-20261008`, agent `hc-44ce62c51e6c4d16b3436512`, pane `w6:p1`, Codex-session `01a11b65-0075-7251-b2bf-59477d00d8f3`. Faktiska processer/cwd och F-05-ägarskap kontrollerades av produktservicerna.

F-12 registrerade korrelerad native ACK **medan Worker arbetade**, före deadline. Worker producerade commit `5e23126269027af55e9fcb0d5a528238129792eb` på `task/f17-epic-f17-task-sixth-attempt`. Ändrade filer: README.md, label_tools.py och tests/test_label_tools.py. Sex verkliga unittest-fall passerade. F-16 läste den avslutade native JSON-rapporten, kontrollerade SHA/rent worktree/komplett diff/epicbas och körde sex tester oberoende med operatörens dokumenterade argv. Handoff `8718167a-6ba7-4d8f-be02-948ca39d5155` satte READY_FOR_REVIEW/Active; ingen merge eller Done utfördes. Upprepad collect gav EXISTING utan databasändring eller omkörning av tester.

| Ref | Före och efter provet |
| --- | --- |
| Fixture main | `7ce621ade256d1f7f6e35d94c9a6348ee21e87fe` |
| Fixture epic | `5027484401e654560164543bbccf2ed5bb308502` |

Samtliga sex testworktrees var rena vid slutavstämningen. F-13 stoppade den godkända test-Worker, verifierade processinaktivitet och frigjorde slot 1. Session, branch, commit och handoff finns kvar. Efter det uttryckliga operatörsstoppet är runtime-tasken PARKED/Attention med resume_state READY_FOR_REVIEW; den historiska verifierade överlämningen är bevarad. Testservern stoppades efter att inga agenter återstod. Implementationstasken F-17 integreras separat genom projektets bootstrap-process.

## Brister upptäckta i verklig runtime

Herdr `agent prompt --timeout` kräver `--wait`. Standardväntan avser settled state och kan konsumera ACK-deadline under implementation. Adaptern använder därför `--wait --until working --timeout`: väntar på arbetsstart, varefter F-12 observerar ACK separat. En returnerad transport är aldrig ensam startbevis.

Installerad Codex rekonstruerar en pågående turn som `interrupted` när en separat, privat app-server läser dess persistenta metadata. F-12 kan använda dess exakta nya userprompt och korrelerade agentACK **endast när native runtime/process/session samtidigt verifierats som working**. Idle/done-projektion, främmande ACK och timeout bekräftar inte start. F-16 kräver fortsatt completed-turn, slutrapport och idle/done runtime för READY. Inga deadlines återställs.

En frivillig Daybreak-banner kan visas trots Herdr interactive_ready. Operatörens provpreflight avfärdade exakt denna frivilliga banner med Esc och väntade på UI-uppdatering. Ingen säkerhetsinställning ändrades; produkten svarar inte automatiskt på approval-, trust- eller frågedialoger. För obevakad drift krävs fortsatt verifierad input-readiness eller operatörspreflight.

Fem föregående, separat namngivna kapabilitetsförsök behålls i SQLite och underlaget: startup/CLI/preflightfel och en riktig kodleverans vars ACK inte bekräftades före deadline. De stoppades genom F-13 före slotrelease. Ingen av dem blev retroaktivt READY/Done och inget timeoutuppdrag skickades om. Den sjätte körningen verifierar den korrigerade produkten; historiken döljs inte genom nya test-ID:n.

## Faktiska behörighetsprov

| Gräns | Prov och resultat |
| --- | --- |
| MCP-roll och scope | Riktig MCP Client/Server med registrerad Worker-principal och produktservicer mot faktisk fixture-SQLite nekade task_start, task_merge/epic_merge-policy, annan task, rollinjektion och främmande rapport. Inga databasändringar. Native Worker anropade inte denna MCP-endpoint under kodarbetet. |
| Eget worktree | Standalone native Codex command/exec med workspaceWrite tillät ofarlig filskrivning. |
| Andra worktrees/utanför root | Samma sandbox nekade ofarlig skrivning, errno 30. Läsning av en uttryckligt ofarlig extern sentinel **tilläts**. |
| Nätverk och Herdr-socket | Portbindning och anslutning till den egna testserverns Unix-socket nekades, errno 1. Inga externa nätförfrågningar gjordes. |
| Processer | Provets operator-PID syntes inte i sandboxens /proc; signal 0 gav ESRCH. Detta är ett avgränsat prov, ingen generell garanti för process-/resursisolering. |
| Delad Git-metadata | Standalone sandbox nekade skrivning av en unik dummy-ref i fixture-repot. Den faktiska Worker fick först read-only-fel vid git add, begärde require_escalated och den begäran accepterades i aktuell approvalmiljö; därefter lyckades staging/commit. Native underlag innehåller begäran och utfallet. Därmed är commit verifierad, men den bevisar **inte** en säker Git-gräns utan eskalering. |
| Oberoende tester | F-16:s minimala subprocess-miljö innehåller PATH/LANG/PYTHONDONTWRITEBYTECODE men har ingen filesystem-sandbox. Separat ofarligt prov kunde skriva utanför task-worktreet. Credentials är inte avsiktligt ärvda, men det skyddar inte läsbara filer på värden. |

Standalone sandboxprovet använder samma installerade workspaceWrite-familj, i ett parkerat testworktree, med striktare tmp-exkludering än native Worker. Det är en separat verklig kapabilitetsmätning; det påstås inte vara samma körningskontext som varje Worker-tool call. Aktuell native policy är on-request/workspace-write/network_access=false; tillfälliga kataloger är inte exkluderade. Promptpolicy och MCP-rollkontroll ersätter inte OS-gränser.

## Hinder före autonom E-10/F-38

Autonom körning/merge får inte aktiveras innan följande verifierats i vald målmiljö:

1. Worker kan inte läsa/skriva orchestratorns skyddade databas, principal/config eller credentials genom shell eller andra lokala tjänster. Nuvarande outside-read lämnar detta osäkrat.
2. Git- och approvalgränsen måste medge egen commit utan att Worker får obevakad eskalering eller möjlighet att ändra främmande refs/worktrees. Den accepterade eskaleringsbegäran här är ingen isoleringsgaranti.
3. Tester från målrepositoryt måste köras med verifierad filesystem/process/nätverksisolering. Minimal miljö ensam räcker inte.
4. Startbanners/input-readiness måste avstämmas före leverans, utan automatiska svar på faktiska approvals.

Nästa ansvariga roll är Coordinator/operatör för målmiljö och betrodd launch-/approvalkonfiguration, följt av Integration för nya faktiska negativa prov. F-17 uppfyller sin avgränsade acceptans genom att redovisa dessa gränser och registrera hindret inför autonom drift.

## Återprov

Använd ett uttryckligen godkänt, ofarligt Git-repository, en separat named Herdr-server och externa test-ID:n. Kontrollera faktisk HERDR_ENV=1 och tillgänglig Codex-inloggning utan att läsa credentials. Starta servern med `herdr --session <eget-namn> server`; använd samma namn i alla kommandon.

Ange Settings med repository/worktree_root/sqlite_path, max_workers=1 och målfixturets dokumenterade `python3 -m unittest discover -s tests -v` som test-argv. Coordinator: F-05 create_epic_worktree, fixturepolicy och ACTIVE. Integration: TaskStartService.start med lokal version-1-spec; gör operatörspreflight av eventuella startdialoger. Poll RuntimeAssignmentService.observe inom 45 sekunder och observera faktisk ACK innan kodleveransen avslutas. Låt aldrig en timeout leda till blind omleverans.

Efter native slutrapport: WorkerReportService.collect, jämför oberoende tester och faktiska main/epic/task-SHA, clean status och andra worktrees. Prova registrerad Worker-roll genom riktig MCP Client/Server och ofarliga sandbox-sentinels. Prova inga riktiga credentials eller främmande resurser. Stoppa enbart egna testagenter genom RuntimeLifecycleService och verifiera inaktivitet före slotrelease. Bevara journaler, sessioner och Git-underlag. Stoppa den egna testservern först när dess agenter är inaktiva.
