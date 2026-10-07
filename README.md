# HerdrCoordinator

HerdrCoordinator ska automatisera utveckling av epics och tasks med Codex-agenter via Herdr, isolerade Git-worktrees och TeamPlayer Kanban. En deterministisk orchestrator ska validera och utföra kritiska operationer samt lagra runtime-information i SQLite. F-01 levererar lokal start, konfigurationsvalidering och sanerad loggning. Databas, MCP och agentautomation levereras i efterföljande tasks.

## Lokal installation och start

Python 3.12+ och [uv](https://docs.astral.sh/uv/) behövs. Utvecklingsmiljön är verifierad på Python 3.13; `.python-version` anger detta val. Kör från det worktree där implementationen finns:

```bash
uv sync --locked
uv run --locked herdr-coordinator --config herdr.example.toml --check
uv run --locked herdr-coordinator --config herdr.example.toml
```

Det första startkommandot validerar konfigurationen och avslutas. Det andra håller grundtjänsten igång tills Ctrl+C eller SIGTERM. I F-01 skapas inga databaser, worktrees eller agentsessioner. JSON-loggar skrivs till stderr, med UTC-tid, nivå, operation och korrelations-ID. Stdout är reserverad för kommande MCP-transport.

Kopiera `herdr.example.toml` till den ignorerade `herdr.local.toml` för lokala val. Relativa paths räknas från konfigurationsfilens katalog. Repository ska vara en befintlig Git-arbetskatalog. Workergränsen är ett heltal 1–2. Worktree-roten får vara utanför repository eller under dess `.worktrees`; den får inte vara repository eller en överordnad katalog. Runtimepaths får inte använda skyddade metadata- eller systemkataloger. SQLite-pathen måste vara skild från worktrees. Symlänkar normaliseras före kontroll; saknade runtimekataloger får ha skrivbara överordnade kataloger.

Credentials ligger i miljövariabler vars **namn** kan anges i `credential_env`. Värden hämtas aldrig från TOML eller skrivs ut. De namngivna värdena maskeras i loggar. Valideringsfel innehåller fältnamn och felbeskrivning, utan råa indata eller exception-dumpar. Exitkod 0 betyder lyckad kontroll/kontrollerat stopp; 2 betyder ogiltig konfiguration.

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
