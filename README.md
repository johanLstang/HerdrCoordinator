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

Underlaget är `reviewable=false` vid smutsig källa/mål, saknad aktuell bas i tasken, ofullständig diff eller observerad ändring under insamlingen. HEAD, arbetsstatus och staged/unstaged-patcharnas hash återkontrolleras. Det är ett läsunderlag, **inte** ett atomiskt lås, godkännande, testresultat eller bevis för merge. F-07/F-08 och senare reviewfeatures måste kontrollera aktuella commits i sina egna skyddade operationer. Ingen runtime-state eller SQLite-schema ändras av läsningen. Diffinnehåll och Git-feloutput skrivs inte till loggar; granskningsmaterial ska hanteras som repositoryinnehåll och inte publiceras osanerat i prompts eller loggar.

## Verifiering och paketering

```bash
uv run --locked pytest
uv run --locked ruff check .
uv build
```

`uv.lock` låser beroendeversionerna. Ett byggt wheel installeras med `python -m pip install dist/herdr_coordinator-0.1.0-py3-none-any.whl` i en separat Python 3.12+-miljö. Startkommandot är därefter `herdr-coordinator --config /path/to/herdr.local.toml`. Herdr-integration finns ännu inte i denna leverans.

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
