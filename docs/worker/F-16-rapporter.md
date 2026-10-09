# F-16: oberoende verifierad Worker-överlämning

`WorkerReportService.collect(actor, task_run_id)` läser slutrapporten via betrodd Herdr/Codex-adapter. Worker-principal måste vara bunden till egen task; Integration kan samla rapporter inom egen epic. Den interna Git-verifieringsrollen härleds från den autentiserade anslutningens verifierade scope. Ingen klient kan ange roll, session, rapporttext, testkommando eller verifieringsbooleans.

## Native proveniens

Registrerad F-11-start och lyckad F-12-ACK krävs. Aktuell pane/process/session/cwd och sparade start-/uppdrags-ID:n kontrolleras. Backendtrådens ID/session/cwd måste matcha. ACK:ns agentitem och levererade userprompt måste fortfarande ha sina sparade SHA256.

Endast ett native agentMessage **efter ACK:n** i en avslutad turn tas emot. Interim commentary används inte; nullable phase stöds för äldre modeller enligt den installerade Codex 0.161.0-scheman. Pågående turn, främmande session, userMessage eller saknad ACK ger ingen överlämning. Den senaste möjliga slutrapporten valideras med [F-14](F-14-kontrakt.md); inget återfall till en äldre rapport när senaste rapporten är felaktig.

Sessions-/turn-/item-ID, meddelandehash och uppdragsoperation sparas. Kort legacy-SHA binds till full **aktuell observerad** task-HEAD, inte till Worker-påstående. Legacy PASS är endast en beskrivning.

## Git och tester före READY

F-05 verifierar worktreeägare och branch. En full stabil källsnapshot kräver aktuell full HEAD som rapportens commit, verifierad taskbas, rent Git-arbetsläge och inga dolda indexflaggor. Angivna filpaths jämförs med faktisk diff; observerade filer sparas separat. Rapportens misslyckade testpåstående avvisas. [F29](../scheduling/F-29-scheduler.md) skiljer denna Worker-källa från aktuell epicreview: basen är F05-taskbas eller senaste faktiska F07-synks inkommande epiccommit. Detta låter B rapportera originalcommiten efter A:s senare epicmerge. F18 synkar och testar sedan det aktuella reviewparet.

Operatören måste ange testkommandot som argv i sin lokala TOML för **målrepositoryt**:

```toml
max_workers = 1
worker_test_command = ["uv", "run", "--locked", "pytest"]
worker_test_timeout = 300
```

Välj målprojektets dokumenterade kommando; inget shell används och Worker-rapportens kommandon exekveras inte. F-07:s verifiering kör operatörens kommando och kontrollerar SHA/worktrees före och efter. Miljön begränsas till PATH, LANG och PYTHONDONTWRITEBYTECODE: orchestratorns token-/GIT-/PYTHON-miljö ärvs inte. Kommando och denna miljö binds i verifieringshashen. Full filesystem-/processisolering av Worker och körd taskkod krävs fortsatt i F-17; miljöbegränsningen ensam bevisar inte den.

Rapportintent sparas före testerna. Native rapport och Git läses igen efter tester; ändrad rapport/HEAD/källbas eller icke godkänd testoperation ger ingen READY. Ett atomiskt state-event och lyckad handoff sparar faktisk commit/källbas, observerad epiccommit, testoperation, argv, exitkod och Git-filer. Verifieringen märks purpose=worker_report/source_base_commit och saknar aktuellt review-target; den kan inte användas för approval. Tasken blir READY_FOR_REVIEW/Kanban Active, håller slot/session och får varken merge eller Done.

## Blockerare och recovery

En native BLOCKED-rapport från WORKING sparar konkret reason/input och state-event. Tasken blir BLOCKED/Attention med resume_state WORKING; slot, session, branch och arbete bevaras. Parkering/release är ett separat verifierat F-13-steg; automatisk Attention-policy kommer senare.

Operationsnyckeln binds till task-run och native proveniens. Duplicerad handoff/reopen återanvänder samma rapport och testbevis. Krasch efter godkända tester men före handoff återanvänder dessa tester. Failed och okänt PENDING testutfall körs inte om blint. Endast betrodd Integration får uttryckligen välja `retry_key` för ett nytt försök; Worker-MCP saknar det argumentet. Ändrad rapport/commit ger nytt underlag som verifieras separat.

Schema 2 är oförändrat: operationskind worker_report, befintlig verify_task och transition_events används. State/event/handoff skrivs i samma SQLite-transaktion. Repo-låset delar serialisering med integration; externa manuella skrivare omfattas inte.

## MCP

Med explicit operatörsvald `--herdr-session` aktiveras `task_report_ready` och `task_report_blocked` tillsammans med F-15-start. Input är endast `{project_id, task_run_id}`. Worker får bara registrera sin egen native rapport. Integration använder den interna collect-tjänsten. Rapporten ska först vara färdig i Codex; ett anrop under pågående turn ger REPORT_TURN_NOT_FINISHED. Utan runtimekonfiguration är MCP fortsatt read-only.

## Prov

Temporärt verkligt Git/SQLite och kontrollerade Herdr/Codex-fakta. Prov täcker korrekt handoff, falska SHA, smutsigt/dolt index, fel task/session/cwd/ACK, pågående/commentary/userMessage, testfel/saknat kommando, sekretessmiljö, legacykort-SHA, rapportbyte under test, krasch/reopen, okänt testutfall, samtidighet och Worker-MCP-scope. Verklig Worker-leverans och full policy följer i F-17.
