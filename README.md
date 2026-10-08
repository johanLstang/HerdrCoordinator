# HerdrCoordinator

HerdrCoordinator ska automatisera utveckling av epics och tasks med Codex-agenter via Herdr, isolerade Git-worktrees och TeamPlayer Kanban. En deterministisk orchestrator ska validera och utföra kritiska operationer samt lagra runtime-information i SQLite. Grundplattformen F-01–F-04 levererar lokal start, konfiguration, sanerad loggning, beständig state, tillståndsregler och en lokal MCP-server. Agentautomation levereras i efterföljande epics.

## Lokal installation och start

Python 3.12+ och [uv](https://docs.astral.sh/uv/) behövs. Utvecklingsmiljön är verifierad på Python 3.13; `.python-version` anger detta val. Kör från det worktree där implementationen finns:

```bash
uv sync --locked
uv run --locked herdr-coordinator --config herdr.example.toml --check
uv run --locked herdr-coordinator --config herdr.example.toml
```

Det första startkommandot validerar konfigurationen och avslutas utan att skapa resurser. Det andra initierar SQLite och håller grundtjänsten igång tills Ctrl+C eller SIGTERM. Inga worktrees eller agentsessioner skapas. JSON-loggar skrivs till stderr, med UTC-tid, nivå, operation och korrelations-ID. Stdout används endast för MCP-transport när `--mcp` anges.

Kopiera `herdr.example.toml` till den ignorerade `herdr.local.toml` för lokala val. Relativa paths räknas från konfigurationsfilens katalog. Repository ska vara en befintlig Git-arbetskatalog. Workergränsen är ett heltal 1–2. Worktree-roten får vara utanför repository eller under dess `.worktrees`; den får inte vara repository eller en överordnad katalog. Runtimepaths får inte använda skyddade metadata- eller systemkataloger. SQLite-pathen måste vara skild från worktrees. Symlänkar normaliseras före kontroll; saknade runtimekataloger får ha skrivbara överordnade kataloger.

Credentials ligger i miljövariabler vars **namn** kan anges i `credential_env`. Värden hämtas aldrig från TOML eller skrivs ut. De namngivna värdena maskeras i loggar. Valideringsfel innehåller fältnamn och felbeskrivning, utan råa indata eller exception-dumpar. Exitkod 0 betyder lyckad kontroll/kontrollerat stopp; 2 betyder ogiltig konfiguration och 3 betyder lagringsfel eller inkompatibelt schema.

## Runtime-lagring (F-02)

`StateStore` lagrar EpicRun, TaskRun, Review, Operation, ExternalReference och TransitionEvent. SQLite-schema 2 använder `PRAGMA user_version`, foreign keys och explicita transaktioner; flera skrivningar kan grupperas med `store.transaction()`. Nästlade operationer använder savepoints. En misslyckad enhet återställs utan partiella rader. F-03 migrerar schema 1 till 2 genom att lägga till transition_events i samma transaktion, utan att skriva om befintliga runs.

Modellfälten lagras som validerad JSON tillsammans med relations- och indexkolumner. Tider är tidszonsmedvetna och normaliseras till UTC. Ett projekt/task-ID får bara ha en ofullbordad ägande run (`completed_at IS NULL`). Historiska avslutade runs kan bevaras. Taskens projekt måste matcha dess epic. Reviewnummer är unika per taskrun. Operationsnycklar är unika per projekt/operationstyp, och externa ID:n får inte bindas till två ägare inom samma projekt/provider/typ. Okända session-, workspace-, agent-, slot- och commitreferenser är null tills ett verkligt delresultat finns.

Schema initieras bara i en tom, oversionerad databas; upprepad start bevarar data. Okänd schemaversion eller ofullständigt schema stoppar start. Ta inte bort databasen för att kringgå detta fel. Senare features levererar fullständiga recoveryflöden. Grundplattformen startar inga agenter och återspelar inga externa operationer.

## Tillstånd och verifieringsgrindar (F-03)

Taskflödet är `PLANNED → CLAIMED → STARTING → WORKING → READY_FOR_REVIEW → REVIEWING → APPROVED → MERGING → DONE`. Review kan ge `CHANGES_REQUESTED → WORKING`. Attention bevarar fasen genom `BLOCKED → PARKED`, och återupptar den sparade fasen med samma session och reserverad kapacitet.

Epicflödet är `PLANNED → ACTIVE → READY_FOR_REVIEW → REVIEWING → APPROVED → MERGING → DONE`. `CHANGES_REQUESTED → ACTIVE` öppnar korrigeringsarbete. Integration lämnar samlad epicacceptans; Coordinator hanterar slutreview och main-merge.

`StateService` kontrollerar aktörens projekt/epic/task och roll, aktuell förväntad state samt övergångens förvillkor. Claim kräver verifierade beroenden; start/resume kräver reserverad slot och bekräftad session; parkering kräver inaktivitet. Taskapproval binds till senaste sparade review och aktuella task/epic-SHA. Done kräver faktisk merge-SHA och passerad verifiering av just den commiten. Epicens finalreview binds till epic/main-SHA. Delvis genomförd merge sparas i MERGING; misslyckade tester tillåter varken Done eller upprepad merge.

State och event skrivs atomiskt. Event-ID är unikt per projekt. Identisk replay returnerar det historiska resultatet utan att ändra aktuell state; samma ID med annan aktör eller annat innehåll avvisas. Läs aktuell runtime separat efter replay. Förlorat nätresultat betyder inte att transitionen behöver utföras igen.

`Actor` och `VerifiedFacts` är interna servicekontrakt. De får inte konstrueras från agentens rollsträng eller egna påståenden om merge, test, slot eller stopp. F-03 verifierar state-reglerna med deterministiska fixtures; verkliga Git/runtime-fakta fastställs av adaptrarna i senare epics. Muterande agentverktyg registreras först när dessa kontroller finns. StateStore är intern persistens, inte ett offentligt sätt att kringgå state-servicen.

## Lokal MCP och behörighet (F-04, beslut D-02)

MCP-värden startar en separat stdio-process för varje operatörsregistrerad aktör:

```bash
uv run --locked herdr-coordinator --config /path/to/herdr.local.toml --mcp --principal /operator/config/worker.json
```

Operatören skapar JSON-profilen utanför Git-repositories och worktrees, med rättigheter `0600`, ägd av processens användare. Dess katalog får inte vara skrivbar av grupp eller andra. Exempel (ersätt run-ID:n med befintliga runtime-ID:n):

```json
{
  "actor_id": "worker-1",
  "role": "Worker",
  "project_id": "d2ee4c75-7b80-465f-83ac-1750854a8e80",
  "epic_run_id": "registered-epic-run",
  "task_run_id": "registered-task-run"
}
```

Rollen är `Worker`, `Integration` eller `Coordinator`. Integration binds till en epicrun och Worker dessutom till sin taskrun. Coordinator binds till projektet. Profilen laddas en gång före databasstart; verktygsargument och klientmetadata kan inte registrera eller byta aktör. Felaktig profil ger exitkod 4. Utan `--principal` kan verktyg upptäckas men alla anrop ger `UNAUTHENTICATED`. `--check` validerar endast TOML-konfigurationen.

Två verktyg publiceras med validerade in- och resultatscheman:

| Verktyg | Argument | Resultat |
| --- | --- | --- |
| `runtime_status` | `project_id` och exakt ett av `task_run_id`, `epic_run_id` | Tillåten runs identitet, state, branch/worktree och runtime-/commitreferenser. |
| `policy_check` | Samma scope samt `operation` | Roll, scope och om operationens service finns; inga ändringar. |

Worker kan bara läsa egen task. Integration kan läsa sin epic och dess tasks. Coordinator kan läsa projektets runs. Rollfält eller andra extra argument avvisas. Svaren innehåller `ok`, `code`, `message`, `data`; fel ger tom `data`. Loggar innehåller beslutskod och registrerad roll, utan råa anropsargument.

| Kod | Betydelse |
| --- | --- |
| `OK` | Tillåten läsning eller policykontroll. |
| `UNAUTHENTICATED` | Anslutningen saknar registrerad aktör. |
| `INVALID_ARGUMENT` | Argumenten följer inte schemat. |
| `FORBIDDEN` | Fel projekt/run eller otillåten roll. |
| `UNKNOWN_OPERATION` | Okänt verktyg eller okänd policyoperation. |
| `NOT_IMPLEMENTED` | Rollen tillåts principiellt, men operationens service saknas. |
| `STATE_UNAVAILABLE` | Lagringen eller sparad state kan inte läsas. |

Policykontrollen känner även till `task_report_ready`, `task_report_blocked`, `task_start`, `task_merge`, `epic_start`, `epic_merge`. Dessa utförs inte och registreras inte som muterande verktyg. Grundplattformen skapar inte runs via MCP och ansluter inte till Herdr eller TeamPlayer.

**D-02:s tillitsgräns:** operatören/MCP-värden måste kontrollera startkommando, profil och databas. Worker får inte kunna skriva dessa eller starta en privilegierad anslutning. Filrättigheter isolerar inte agenter som delar samma OS-användare. Verkliga runtime-/sandboxgränser verifieras i F-10/F-17 innan autonom drift i F-38; denna leverans verifierar anslutningens behörighet och lokal MCP-transport.

## Git-worktree-skapande (F-05)

`WorktreeService` använder en intern, betrodd `Actor` från registrerad startkontext. Coordinator kan skapa epics inom sitt projekt; Integration kan skapa tasks i sin registrerade epic. Worker saknar denna behörighet. Servicen är ännu inte ett offentligt muterande MCP-verktyg.

```python
with StateStore(settings.sqlite_path) as store:
    service = WorktreeService(settings, store)
    epic = service.create_epic_worktree(
        coordinator_principal, epic_id="E-02", run_id="epic-run-id"
    )
    task = service.create_task_worktree(
        integration_principal,
        epic_run_id=epic.id,
        task_id="F-05",
        run_id="task-run-id",
    )
```

`coordinator_principal` och `integration_principal` är operatörsregistrerade profiler; den senare är bunden till `epic.id`. Repository i settings ska vara main-worktreets rot. Ny epic kräver ren main och skapas från dess aktuella HEAD; ny task kräver sin verifierade, rena epic och skapas från dess aktuella HEAD. Nya tasks tillåts i PLANNED, ACTIVE och CHANGES_REQUESTED, och stoppas när epicen går vidare till slutreview eller integration. Befintliga framgångsrika resurser återläses utan att ändra deras innehåll, även efter Worker-commits eller ocommittat arbete.

Lokala ID:n `E-02`/`F-05` ger branches `feature/epic-e02` och `task/e02-f05`. UUID:n bevarar bindestreck. IDs i path/branch får innehålla bokstäver, siffror och bindestreck, högst 80 tecken; de normaliseras till gemener för Git-namn, men den exakta identiteten sparas i SQLite. Namnkollision innebär fel. Paths är `<worktree_root>/<project-id>/epic-<epic>` respektive `task-<epic>-<task>`. Ett explicit pathargument måste matcha samma normaliserade path; traversal och symlänkar utanför roten avvisas.

Skapande lagrar run och `Operation(PENDING)` med repository/common-dir, branch, path och exakt bas-SHA **före** Git-mutation. Operationens UUID registreras som lokal branchägarmarkör `branch.<branch>.herdrOwner` före `git worktree add`. Därefter verifieras worktree, branch, ägare och HEAD; current_commit och `Operation(SUCCEEDED)` sparas atomiskt. Schema 2 används utan migration. SQLite-transaktioner serialiserar skapande även mellan separata serviceanslutningar.

Vid avbrott bevaras intent, ägarmarkör och kända Git-resurser. Samma run-ID återanvänder en verifierad branch/worktree, även efter processomstart. Befintlig branch utan rätt ägarmarkör adopteras inte. Om en färdig resurs saknas, en ofärdig branch har ändrats eller källbasen ändrats innan resursen skapats stoppas återförsöket för avstämning. Servicen raderar, återställer eller force-checkar inte något arbete. Initial base_commit bevaras; färsk Git-status levereras av F-06.

Git-anrop använder separata argv-argument, sanerad Git-miljö och timeout. Checkout-hooks och fsmonitor är avstängda; fel visar inte Git-output eller råa paths. `WorktreeError` avser policy/ägarskap/path/recovery, `GitError` Git-förvillkor eller transport/processfel och `StoreError` persistens. Dessa interna fel ska hanteras av kommande orchestratorflöden. Branchägarmarkören och SQLite måste ligga utanför Workers skrivbehörighet enligt D-02; detta prov ersätter inte kommande runtime-sandboxverifiering.

## Git-underlag för granskning (F-06)

`GitAdapter.snapshot(path, branch, base, expected_commit=..., max_diff_bytes=...)` läser en registrerad worktree i konfigurerat repository. Commits anges som fullständiga SHA-1-ID:n; revisionsuttryck, okända objekt, fel branch/worktree och främmande repositories avvisas. `require_commit` verifierar ett commitobjekt och `contains_commit` kontrollerar branchens innehåll. Ingen av dessa operationer mergear eller godkänner arbete.

Snapshoten innehåller repository/common-dir, worktree, branch, bas/current-SHA, om aktuell branch innehåller basen, ändrade filer med status och radantal samt binärmarkering. Filnamn läses med NUL-separation. `commit_diff` gäller endast de två angivna commits; `staged_diff`, `unstaged_diff` och `changes` visar arbetsläget separat. Ospårade filer redovisas i `changes` och inkluderas inte som om de vore committade. Rename i arbetsläget bevarar både gammal och ny path; commitdiff visar delete/add utan heuristisk rename-detektion. Binärfiler har en binär Git-patch och inga påhittade radantal.

Diffar lagras tillfälligt i en privat, automatiskt borttagen fil och returneras som bytes. Standardgränsen är **1 MiB per diff**, med valbar gräns 1 byte–64 MiB. `total_bytes` och `sha256` gäller hela patchen; `complete=false` betyder uttryckligen att `patch` bara är ett förhandsutdrag. Höj `max_diff_bytes` upp till totalstorleken för att hämta hela diffen inom 64 MiB. För större underlag kan `full_command` köras som argumentlista i `worktree_path`, med Git-miljövariabler borttagna och output strömmad till en skyddad fil; kontrollera byteantal och SHA-256 mot snapshoten. Kommandot för commitdiffen använder redan verifierade, fasta commit-ID:n. Staged/unstaged-underlag gäller endast det observerade arbetsläget och måste hämtas om vid ändring.

`GitReviewService(settings, store).task_review(actor, task_run_id, expected_commit=...)` ger Integration inom rätt epic eller Coordinator ett taskunderlag mot **aktuell epic-HEAD**. `epic_review(actor, epic_run_id, ...)` tillåter bara Coordinator och jämför mot aktuell main. Servicen verifierar beständig skapelseavsikt och Git-ägarmarkör från F-05 före och efter läsningen. Worker eller fel project/epic får inget reviewunderlag. API:t är internt; det registreras inte som nytt MCP-verktyg i denna task.

Underlaget är `reviewable=false` vid smutsig källa/mål, saknad aktuell bas i tasken, ofullständig diff eller observerad ändring under insamlingen. HEAD, arbetsstatus och staged/unstaged-patcharnas hash återkontrolleras. Indexflaggorna assume-unchanged/skip-worktree redovisas i `unsafe_index_paths` (och `target_unsafe_index_paths`) och förhindrar reviewable eftersom de kan dölja arbetsändringar. Submoduleändringar inkluderas uttryckligen även om Git-konfigurationen vill ignorera dem. Det är ett läsunderlag, **inte** ett atomiskt lås, godkännande, testresultat eller bevis för merge. F-07/F-08 och senare reviewfeatures måste kontrollera aktuella commits i sina egna skyddade operationer. Ingen runtime-state eller SQLite-schema ändras av läsningen. Diffinnehåll och Git-feloutput skrivs inte till loggar; granskningsmaterial ska hanteras som repositoryinnehåll och inte publiceras osanerat i prompts eller loggar.

## Synkronisering och taskintegration (F-07)

`GitIntegrationService(settings, store, test_command=(...))` är ett internt API för en betrodd, registrerad Integration i rätt epic. Worker och Coordinator får inte utföra dess taskoperationer. Testkommandot och timeouten är operatörskonfiguration; de får inte hämtas från en agentrapport eller nya MCP-argument. Testprocessens output lagras/loggas inte. Schema 2 används utan migration.

1. `sync_task_with_epic(actor, task_run_id, key=...)` synkar aktuell Epic → Task. Det är separat från leverans, ogiltigförklarar äldre approval vid ändrat underlag och bevarar konflikter i task-worktreet. En tidigare Approved-task går till ChangesRequested när reviewn måste göras om. Ingen reset, abort eller cleanup utförs automatiskt.
2. `verify_task(actor, task_run_id, key=...)` kör det konfigurerade testkommandot som argv i task-worktreet. Operationen sparar faktiska source/target-SHA, kommandots hash och exitkod. Kod, bas eller arbetsläge som ändras under testkörningen ger FAILED/STALE_TEST_EVIDENCE. En okänd utgång efter processavbrott kräver en ny uttrycklig verifiering; en tests_passed-flagga godtas inte.
3. `register_task_review(actor, task_run_id, verification_key=..., key=..., approved=True/False, feedback=...)` registrerar den manuella granskarens beslut mot komplett, aktuellt Git-underlag och en lyckad verklig testoperation. Det automatiserar inte reviewbeslutet. Runtime-handoff ska ha nått ReadyForReview/Reviewing; efter ChangesRequested lämnar Worker nytt underlag enligt normal livscykel. Återläsning av ett äldre review-ID ger dess ursprungliga beslut/SHA, inget nytt godkännande.
4. `merge_task_to_epic(actor, task_run_id, key=...)` kräver senaste registrerade approval/testoperation för exakta, aktuella task- och epiccommits, synkroniserad task, rent arbetsläge och F-05-ägarskap. Leveransmerge använder `--no-ff` och sparar merge-SHA på operation/task samt aktuell epic-HEAD. Tasken lämnas i MERGING; varken lokal Kanban, TeamPlayer eller runtime-task blir Done genom enbart Git-merge. Relevant verifiering på mergecommiten och senare Done-service återstår.

Nycklar är stabila per avsedd operation och får inte återanvändas för annan task eller ändrade test-/reviewindata. En privat låsfil i repositorys common-dir serialiserar integration mellan serviceinstanser/processer. SQLite-transaktioner skyddar state; en PENDING-Operation och MERGING/invaliderad approval sparas **före** Git. Git-commiten märks med operationens UUID. Efter avbrott verifieras markör, exakt parentpar och branchinnehåll innan det ursprungliga merge-SHA:t återregistreras; ingen andra merge skapas. En lyckad operation kan återläsas även när targetbranchen avancerat, och `current_destination_commit` anger observerad HEAD vid registrering, separat från ursprungligt merge-SHA; återläsning av en lyckad operation returnerar dess historiska observation, inte en ny Git-snapshot. Ändrad source efter Git-resultatet markeras `requires_reconciliation`; inga sådana fakta får användas som ny Done-evidens.

Konflikt ger beständig CONFLICT/MERGE_CONFLICT och task-BLOCKED, utan att slänga index, konfliktfiler eller annan Git-state. Ett oförklarat branch-/ägarskapsbyte stoppas för avstämning. Externa merge-/filterprogram avvisas för operatörsbeslut; hooks, fsmonitor, autostash, rerere och commit-signering körs inte av mergeoperationen. Repo-låset skyddar samverkande services; externa manuella Git-skrivningar och Workers åtkomst till Git/SQLite måste dessutom begränsas av D-02:s runtimegräns. Detta är inte full scheduling, generell konfliktlösning eller en automatisk reviewagent.

F-07-proven använder riktiga Git-repositories, SQLite och subprocesser med små fixture-testkommandon. Enbart runtime-handoffens faser är fixtures; ingen verklig Codex-session eller Worker-runtime påstås vara verifierad här.

## Verifiering och paketering

```bash
uv run --locked pytest
uv run --locked ruff check .
uv build
```

`uv.lock` låser beroendeversionerna. Ett byggt wheel installeras med `python -m pip install dist/herdr_coordinator-0.1.0-py3-none-any.whl` i en separat Python 3.12+-miljö. Startkommandot är därefter `herdr-coordinator --config /path/to/herdr.local.toml`. Herdr-runtime kopplas genom operatörens explicita MCP-konfiguration enligt Worker-start nedan.

### Teknikbeslut D-01

Paketet heter `herdr-coordinator`, med importpaket `orchestrator` under `src`. Basen är Python 3.12+, asyncio för tjänstens livscykel, Pydantic 2 för validerade modeller, TOML via standardbiblioteket och sqlite3 för kommande persistens. SQLAlchemy och ett workflow-framework behövs inte för grundplattformen. Pytest verifierar beteende, Ruff kontrollerar kod och Hatchling bygger wheel/sdist. Exakta installerade versioner finns i `uv.lock`.

## Projektdokumentation

- [Arbetsprocess](Herdr_Workflow.md) — agentroller, task-livscykel, review och merge.
- [Arkitektur och implementationsplan](Arkitektur%20och%20implementationsplan%20%E2%80%93%20Herdr%20-Codex%20Multi-Agent%20Workflow.md) — komponenter, runtime-kontrakt och implementationsfaser 1–12.
- [Guide för epics och features](Epic%26Feature%20Guide.md) — projektets mall för backlogg, körbara Worker-uppdrag och verifierbar acceptans.
- [Komplett backlogg](Backlog.md) — 12 epics och 50 tasks med beroenden, arbetsinstruktioner, acceptans och Kanbanöversikt.
- [Utvecklingsprocess](Utvecklingsprocess.md) — en feature i taget, plockning i TeamPlayer, taskintegration och epic-PR.
- [Agentinstruktioner](AGENTS.md) — gemensamma regler med kompletteringar i src, tests och prompts.

Använd guiden när nya epics och features planeras. TeamPlayer är primär källa för arbetsstatus när kopplingen är etablerad; dokumentöversikten speglar den. En task blir `Done` efter godkänd review, merge till epic-branchen och integrationstester. En epic blir `Done` efter slutreview, merge till `main` och slutverifiering.

Epics använder `Planned` före start, `Active` under arbete och hela review/integrationsflödet, och `Done` efter verifierad leverans till main. Hinder dokumenteras separat medan en påbörjad epic behåller Active. Coordinator synkar epicstatus via TeamPlayers `update_epic_status` med färsk version från `list_epics`; API-statusarna Pending/InProgress/Done motsvarar Planned/Active/Done. Tasks kan dessutom ha Attention.

### Epic → main (F-08)

`EpicIntegrationService` återanvänder F-07:s repo-lås, operationer och Git-adapter.
Betrodd operatör konfigurerar `expected_task_ids` (externa task-ID:n för hela
scope), `test_command` som argv och timeout. Agenten kan inte välja dessa i ett
verktygsanrop. `verify_epic` kräver samtliga tasks Done, registrerade F-07-review/
tester och faktisk operationstagg, mergeparents samt ancestry för varje leverans.
Komplett diff och aktuella epic/main-SHA kontrolleras före och efter testprocessen.

Coordinator registrerar ett manuellt beslut med `register_epic_review`, bundet
till verifieringsnyckeln och taskmanifestet. `merge_epic_to_main` kontrollerar
underlaget igen, skriver Pending-intent och utför `--no-ff`. Resultatet binds till
operationstagg och exakta parents för återhämtning före/efter processavbrott.
Ändrad main kräver explicit `sync_epic_with_main`, nya tester och review. Synk går
Main → Epic och ändrar inte main. Konflikter bevaras och måste hanteras manuellt.

Efter merge kör `verify_main_merge` faktisk verifiering på exakt registrerad
main-merge. Testfel sparas med exitkod; återförsök med ny verifieringsnyckel kör
endast tester och upprepar ingen merge. Epicen lämnas `MERGING` även vid godkänd
slutverifiering: completion/TeamPlayer-tjänsten måste senare kontrollera bevisen
innan Done. Automatisk Coordinator-review och nästa-epic-loop införs i fas 10.
Ingen schemaändring, runtime-start eller extern statusmutation görs här.

### Worktree-cleanup och retention (F-09)

`TaskCleanupService.remove_task_worktree` är ett explicit internt anrop från
betrodd Integration, bundet till projekt/epic/task. Tasken ska vara Done med
registrerad review/test och faktisk merge/parents/ancestry i epicen. Servicen
kontrollerar ägarskap, exakt branch-HEAD, exklusiv runkoppling och rent arbetsläge.
Även ignored-filer, dolda indexflaggor, Gitoperationer och worktree-lås blockerar.
Ingen `--force`, reset, abort, global prune eller rekursiv filradering används.

**Retention:** bara task-worktreet tas bort. Alla branches, ägarmarkörer,
run-/review-/operation-/mergehistorik och externa referenser behålls. Epic-worktrees
och branches städas inte automatiskt. Brancharkivering/radering kräver en separat
operatörspolicy och verifierad backup; F-09 inför ingen sådan radering.
Utvecklingens äldre bootstrap-worktrees används inte som cleanup-testresurser.

En reserverad Worker-slot blockerar alltid. Registrerad agent, workspace, session
eller runtime-reference kräver att en betrodd `inactivity_probe(TaskRun)` returnerar
exakt `True`. Saknad, felande eller okänd probe skyddar resursen. F-09 stoppar ingen
session och frigör ingen slot; verklig Herdr-probe/stopp kopplas in efter F-10/F-13.
Prov med en probe-fixture visar policyn, inte verklig runtimeinaktivitet.

Pending-intent sparas före Git. Retry efter lyckad borttagning stämmer av kvarvarande
branch/ägarskap och frånvarande worktree; historiken ändras inte. En återuppstånden
resurs tas inte bort igen. Saknad resurs utan tidigare cleanup-intent/resultat är
ett avstämningsfel. Avbrott mitt i Git-remove med delvis kvarvarande path/registry
kräver manuell avstämning; automatisk prune/radering används inte. Ignored-filer
måste säkras eller tas bort av operatören före cleanup. Låset samordnar tjänsterna;
oberoende manuella Git-/filsystemskrivare omfattas inte.


### Verifierad runtime-start (F-11)

Den interna `RuntimeStartService` kopplar registrerade task-/epicworktrees till
operatörens explicita Herdr-session. Integration startar CLAIMED tasks, Coordinator
startar aktiva epics. Taskslot och STARTING-intent sparas före externa anrop;
Codex körs med `--no-daemon`. Upprepade starter återläser samma ägda resurser.
Okänd workspace-skrivning kräver avstämning, och en pending agentstart observeras
utan blind ny start. Produktuppdrag/WORKING, park/stopp och slotrelease ingår i
senare services. MCP-servern är fortfarande read-only.

Se [startkontrakt och recovery](docs/runtime/F-11-start.md) samt
[verkliga gränssnittsprov](docs/runtime/Herdr-Codex-kontrakt.md).

## Uppdrag och startbekräftelse (F-12)

Internt `RuntimeAssignmentService` skickar en beständig, korrelerad uppdragsprompt till registrerad Herdr/Codex-runtime. WORKING kräver ett matchande native agentmeddelande och verifierad promptleverans; timeout och okänt transportutfall ger ingen falsk start. Upprepning observerar samma operation. Se [uppdrags-/statuskontrakt](docs/runtime/F-12-uppdrag.md) och [verkliga prov](docs/runtime/F-12-prover.json). MCP är fortsatt read-only.

## Runtime-livscykel (F-13)

Internt `RuntimeLifecycleService` återansluter till ägd runtime, bekräftar stopp med processidentiteter/barn/grupper före slotrelease och återupptar samma sparade Codex-session efter kapacitetskontroll. Se [stopp-/resume-kontrakt](docs/runtime/F-13-livscykel.md) och [verkliga prov](docs/runtime/F-13-prover.json). Automatisk Attention-policy och full Worker-sandbox återstår i senare features.

## Worker-uppdrag och rapportformat (F-14)

Version 1 validerar lokal taskspec, binder den till registrerad task/epic och bygger en reproducerbar Worker-prompt med paketerad policy. Rapportparsern kräver betrodd sessionsproveniens och normaliserar äldre textformat utan att godkänna Git-/testpåståenden. Se [D-04, schema och exempel](docs/worker/F-14-kontrakt.md). Runtime-start levereras i F-15 och oberoende rapportverifiering i F-16.

## Explicit Worker-start (F-15)

`TaskStartService` binder en lokal version-1-task till en unik run och reserverar slot före Git/Herdr. F-11/F-12 utför start och korrelerad native ACK. Återförsök behåller samma resurser och prompt. MCP `task_start` aktiveras endast med operatörens explicita `--herdr-session` och skyddade Integration-principal; utan det är servern read-only. Fas 4 använder en Worker. Se [start, MCP och recovery](docs/worker/F-15-start.md).

## Verifierad Worker-rapport (F-16)

`WorkerReportService` hämtar native slutrapport från registrerad Codex-session och kontrollerar ACK/proveniens, aktuell commit och rent worktree. Operatörens `worker_test_command` körs oberoende innan READY_FOR_REVIEW/Active; rapportens PASS och testkommandon är påståenden. BLOCKED sparar reason/input utan slotrelease. Explicit runtime-MCP har `task_report_ready/blocked` för eget Worker-target. Se [rapport-, test- och recoverykontrakt](docs/worker/F-16-rapporter.md).

## Aktuell taskreviewkontext (F-18)

`TaskReviewService` synkroniserar en verifierad Worker-leverans mot aktuell epic och kör operatörens testkommando på det nya versionsparet. Komplett diff, taskspecifikation, acceptans, versionerade källor, epicregler och testoperation sparas med context-ID och exakta task/epic-SHA. Först därefter går tasken till REVIEWING. MCP `task_review_request` aktiveras när både `review_context` och `worker_test_command` finns i operatörens TOML; endast registrerad Integration inom rätt epic får anropa det. Ingen Herdr-session startas implicit. Underlaget ger inget approval, leveransmerge eller Done. Se [konfiguration, gränser och recovery](docs/review/F-18-kontext.md).

## Korrigering i samma Worker-session (F-19)

`TaskChangesService` validerar ett negativt granskningsbeslut mot aktuellt F-18-underlag och befintliga acceptanskriterier. Reviewnummer, commits och numrerad feedback sparas före leverans till registrerad Worker. CHANGES_REQUESTED ligger i Active; WORKING kräver korrelerad native ACK i samma Codex-session, branch och worktree. Nästa rapport måste komma efter ACK och verifieras på nytt genom F-16/F-18. MCP `task_request_changes` kräver både reviewkonfiguration och explicit `--herdr-session`; det ger inget approval, leveransmerge eller Done. Se [policy, schema och recovery](docs/review/F-19-korrigering.md).

## Versionsbundet taskgodkännande (F-20)

`TaskApprovalService` kräver registrerad Integration, senaste kompletta reviewkontext, aktuell verklig verifiering och ett positivt beslut för samtliga acceptanskriterier. Approval sparar granskare, context/review/test-ID:n och exakt task/epic-par. Ändrad kod, bas, konfiguration eller ersatt context avvisar användning av äldre approval och bevarar historiken. MCP `task_approve` aktiveras med reviewkonfiguration; det startar ingen runtime och mergear inte. APPROVED ligger kvar i Active med slotreservation. Se [beslut, kontroll och recovery](docs/review/F-20-godkannande.md).

### Verifierad taskleverans (F-21)

`task_merge` kräver registrerad Integration, F-20:s aktuella kompletta approval och explicit Herdr-session. Servicen gör Task → Epic med `--no-ff`, testar exakt merge-SHA och bekräftar fysisk Worker-exit före slotrelease och Done. Stabil leveransnyckel och separat verifieringsnyckel gör att fel/omstart återanvänder känd merge. Samma misslyckade/okända testförsök körs inte om; ny verifieringsnyckel begär explicit eftertest. `DELIVERY_BUSY` kräver återläsning/återförsök med samma nycklar efter pågående operation. Cleanup är ett separat explicit F-09-anrop efter Done, med faktisk F-13-inaktivitetsprobe; worktree, branches och historik bevaras tills resurspolicyn tillåter borttagning. Se [leverans, stopp och recovery](docs/review/F-21-leverans.md). TeamPlayer-produktadapter följer i E-06 och samlat native prov i F-22.

## Verkligt review- och leveransprov (F-22)

En avgränsad native harness provar draft → negativ review → korrigering i samma
Worker-session → aktuellt godkännande → taskmerge/test/fysiskt stopp/Done samt ett
separat faktiskt blockerande testfall. Operatören driver Integration-rollen enligt
paketerad policy; fixture-main hålls oförändrad. Se
[prov, återkörning och begränsningar](docs/review/F-22-native.md). Använd alltid
ett separat godkänt repository och en namngiven testserver.

## TeamPlayer-kontrakt (F23)

Verklig MCP-anslutning, schemas och avgränsade fixtureprov beskrivs i
[TeamPlayer-kontraktet](docs/teamplayer/F-23-kontrakt.md). Konto, externa ID:n,
versionskonflikter, numerisk prioritet och beskrivningshistorik är verifierade;
produktreader och beständig statussynk följer i F24/F25. Credentials hålls i lokal
operatörskonfiguration. Testepicen och dess tasks är separata från implementationen.

## TeamPlayer-läsare (F24)

Internt `TeamPlayerReader` validerar kontot, hela boardgrafen och explicita lokala
ID-/källbindningar. Ofullständiga listor, cykler, saknade tasks, fel tilldelning eller
ändrade snapshots ger fel/ej körbara kandidater. Läsningen startar ingen Worker och
ändrar inga externa statusar eller runtime-records. Se [API och verifiering](docs/teamplayer/F-24-lasning.md);
operatörens avgränsade native läsprov är `uv run --locked python scripts/probes/f24_read.py`.

## TeamPlayer-synk (F25)

`TeamPlayerSyncService` speglar verifierade domänhändelser med beständig outbox och
explicit UUID-bindning. Integration synkar tasks och Coordinator epics; Worker
nekas före nätanrop. En operatör kan injicera servicen i `RuntimeService` för
MCP-anropen `set_task_status`/`set_epic_status`; anroparen väljer bara run-ID.
Status och sanerad historik härleds från faktiska start-, review-, Git-, test-
och stoppbevis. Återläsning efter nätfel återförsöker bara TeamPlayer-steget.
Se [API, statusgrindar och recovery](docs/teamplayer/F-25-synk.md).
