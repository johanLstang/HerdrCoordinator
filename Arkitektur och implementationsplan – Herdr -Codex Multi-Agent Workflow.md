# Arkitektur och implementationsplan – Herdr/Codex Multi-Agent Workflow

## 1. Mål

Systemet ska automatisera utveckling av Epics och Tasks med hjälp av Codex-agenter som körs parallellt genom Herdr.

Arbetsmodellen bygger på tre agentroller:

- **Epic Coordinator**
- **Epic Integration Agent**
- **Worker Agent**

Systemet ska:

- hämta Epics och Tasks från TeamPlayer Project via MCP,
- skapa Git branches och worktrees automatiskt,
- starta Codex-sessioner i rätt worktree via Herdr,
- köra maximalt två aktiva Worker Agents parallellt,
- automatiskt reviewa och integrera Tasks,
- hantera blockerade Tasks via `Attention`,
- genomföra slutreview av en Epic,
- mergea godkända Epics till `main`,
- fortsätta med nästa Epic,
- kunna återhämta sig efter omstart.

---

# 2. Arkitekturprincip

LLM-agenter ska fatta beslut.

Deterministiska komponenter ska utföra kritiska operationer.

```text
Agent:
"Task 123 är redo att startas"

        │
        ▼

Orchestrator:
- validerar state
- skapar branch
- skapar worktree
- startar Herdr-session
- startar Codex
- uppdaterar state
- uppdaterar Kanban
```

Agenten ska därför inte behöva bygga komplex shell-logik själv.

Det minskar risken för:

- fel branch,
- fel worktree,
- dubbla Workers,
- felaktig merge,
- förlorad session,
- inkonsekvent Kanban-status.

---

# 3. Målarkitektur

```text
                     TeamPlayer Project
                      Kanban / MCP API
                            │
                            │
                            ▼
                 ┌──────────────────────┐
                 │   Epic Coordinator   │
                 │        Codex         │
                 └──────────┬───────────┘
                            │
                            │ MCP tools
                            ▼
              ┌───────────────────────────┐
              │     Agent Orchestrator    │
              │                           │
              │ Python service / MCP      │
              └─────────────┬─────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
          ▼                 ▼                 ▼
      Git Manager      Herdr Adapter      State Store
          │                 │               SQLite
          │                 │
          ▼                 ▼
       branches         workspaces
       worktrees        panes
       merge            Codex sessions
                            │
                            ▼
                 ┌──────────────────────┐
                 │ Epic Integration    │
                 │ Agent / Codex       │
                 └──────────┬───────────┘
                            │
                   ┌────────┴────────┐
                   │                 │
                   ▼                 ▼
             Worker Agent 1    Worker Agent 2
                  Codex             Codex
                   │                 │
                   ▼                 ▼
                Task A            Task B
               worktree          worktree
```

---

# 4. Komponenter

## 4.1 Epic Coordinator

Epic Coordinator är en långlivad Codex-session.

Coordinatorn ansvarar för projektnivån.

Den:

- väljer nästa prioriterade Epic,
- initierar Epic runtime,
- startar Epic Integration Agent,
- väntar på färdig Epic,
- gör slutreview,
- begär korrigering vid behov,
- mergear Epic till `main`,
- markerar Epic `Done`,
- går vidare till nästa Epic.

Coordinatorn äger:

```text
main
```

Coordinatorn ska inte normalt implementera kod.

---

# 4.2 Epic Integration Agent

En ny Integration Agent startas för varje Epic.

Den lever under hela Epicens livslängd.

Den ansvarar för:

- Epicens Tasks,
- prioritering,
- beroenden,
- Worker slots,
- task worktrees,
- Worker Agents,
- task review,
- fix-loopar,
- task merge,
- integrationstester.

Integration Agent äger:

```text
feature/epic-*
```

Den får inte mergea till `main`.

---

# 4.3 Worker Agent

En Worker Agent motsvarar normalt en Task.

Den:

- implementerar Tasken,
- kör tester,
- committar ändringar,
- rapporterar blockerare,
- tar emot reviewfeedback,
- gör korrigeringar.

Worker Agent äger:

```text
task/*
```

Worker Agent får aldrig mergea.

---

# 4.4 Agent Orchestrator

Detta är den viktigaste tekniska komponenten.

Jag rekommenderar att den implementeras i Python.

Exempel:

```text
herdr-orchestrator/
│
├── orchestrator/
│   ├── coordinator.py
│   ├── epic_runtime.py
│   ├── task_runtime.py
│   ├── scheduler.py
│   └── state_machine.py
│
├── adapters/
│   ├── teamplayer_mcp.py
│   ├── herdr.py
│   ├── git.py
│   └── codex.py
│
├── storage/
│   ├── models.py
│   └── sqlite.py
│
├── prompts/
│   ├── coordinator.md
│   ├── integration_agent.md
│   └── worker.md
│
├── policies/
│   ├── git_policy.py
│   ├── merge_policy.py
│   └── review_policy.py
│
└── main.py
```

Orchestratorn ska vara deterministisk.

Den får instruktioner från agenter men ansvarar själv för att operationerna utförs säkert.

---

# 5. Orchestrator som MCP-server

Jag rekommenderar starkt att Orchestratorn exponeras som ett lokalt MCP-interface.

Codex-agenterna får då verktyg som:

```text
epic_claim_next()
epic_start()
epic_complete()

task_get_next()
task_start()
task_report_blocked()
task_report_ready()

task_review_request()
task_request_changes()
task_merge()

worker_start()
worker_resume()
worker_stop()
```

I stället för att Integration Agent gör:

```bash
git worktree add ...
herdr workspace create ...
herdr agent start ...
```

kan den göra:

```text
task_start(task_id=123)
```

Orchestratorn gör resten.

Det ger en mycket säkrare arkitektur.

---

# 6. TeamPlayer MCP Adapter

Denna komponent kommunicerar med TeamPlayer Project.

Logiska operationer:

```text
get_next_epic()

get_epic(epic_id)

get_epic_tasks(epic_id)

set_epic_status()

set_task_status()

add_task_comment()

get_task_dependencies()
```

Exakta MCP tool names kapslas in av adaptern.

Epicens boardstatus använder endast Planned, Active och Done. Coordinator skriver den vid start och efter slutleverans, och stämmer av efter taskintegration samt vid review/hinder. Påbörjade epics förblir Active under samtliga interna review-, korrigerings- och mergefaser, paus och blockerare. Done kräver samlad acceptans, slutreview, main-merge och slutverifiering. Taskernas Attention är separat och blir inte epicstatus.

Verifierat TeamPlayer-kontrakt: `list_epics(projectId)` returnerar epicens `id`, `status`, `version`; `update_epic_status(projectId, epicId, version, status)` skriver med optimistic concurrency. API-status Pending motsvarar Planned, InProgress motsvarar Active och Done motsvarar Done. Återläs efter skrivning, versionskonflikt eller okänt nätutfall. Saknat verktyg i en äldre klientlista kräver kontroll av serverns aktuella MCP-katalog. En statussträng ersätter inte Git-/testbevis.

Resten av systemet ska därför inte behöva känna till TeamPlayer MCP:s interna API.

---

# 7. Herdr Adapter

Herdr Adapter ansvarar för:

```text
create workspace
create pane
create worktree workspace

start Codex
resume Codex

send prompt

get agent status

wait for status

terminate agent
```

Den ska också lagra Herdr-ID:n:

```text
workspace_id
pane_id
agent_id
session_id
```

så att en session kan återanslutas efter en omstart.

---

# 8. Git Manager

Git Manager ska vara den enda komponent som utför kritiska Git-operationer.

Exempel:

```text
create_epic_branch()

create_task_branch()

create_worktree()

sync_task_with_epic()

get_diff()

merge_task_to_epic()

merge_epic_to_main()

remove_worktree()

delete_branch()
```

Före varje operation validerar komponenten:

```text
expected branch
expected worktree
clean state
current HEAD
allowed merge direction
```

---

# 9. Branchmodell

```text
main
 │
 └── feature/epic-123
       │
       ├── task/123-1
       ├── task/123-2
       ├── task/123-3
       └── task/123-4
```

Tillåtna merge-riktningar:

```text
task/* → feature/epic-*

feature/epic-* → main
```

Alla andra automatiska merges förbjuds.

---

# 10. Worktree-struktur

Worktrees bör lagras på en förutsägbar plats.

Exempel:

```text
~/worktrees/
└── Anemoria/
    ├── epic-123/
    ├── task-123-1/
    ├── task-123-2/
    └── task-123-3/
```

eller för TeamPlayer:

```text
~/worktrees/
└── TeamPlayer/
    ├── epic-42/
    ├── task-421/
    └── task-422/
```

Det gör cleanup och felsökning enklare.

---

# 11. Lokal state store

Kanban är business source of truth.

Men Kanban innehåller inte tillräckligt med teknisk runtime-information.

Därför bör Orchestratorn även använda SQLite.

Exempel:

```text
orchestrator.db
```

---

# 12. EpicRun

`status` nedan är intern runtime-state. PLANNED mappar till epicens Planned, ACTIVE/READY_FOR_REVIEW/REVIEWING/CHANGES_REQUESTED/APPROVED/MERGING till Active och verifierad DONE till Done. Detaljerade interna tillstånd bevaras i SQLite även när flera faser visas som Active på boarden.

```text
EpicRun
---------------------
id
project_id
epic_id

branch
worktree_path

status

integration_agent_id
herdr_workspace_id
codex_session_id

started_at
completed_at
```

---

# 13. TaskRun

```text
TaskRun
---------------------
id
epic_run_id
task_id

branch
worktree_path

internal_status
kanban_status

worker_agent_id
worker_slot

herdr_workspace_id
codex_session_id

base_commit
current_commit

started_at
completed_at
```

---

# 14. Review

```text
Review
---------------------
id
task_run_id

review_number
review_result

review_commit
epic_commit

feedback

created_at
```

Det gör reviewhistoriken spårbar.

---

# 15. Intern Task state machine

```text
PLANNED
   │
   ▼
CLAIMED
   │
   ▼
STARTING
   │
   ▼
WORKING
   │
   ├─────────────► BLOCKED
   │                   │
   │                   ▼
   │                 PARKED
   │                   │
   │                   ▼
   │                WORKING
   │
   ▼
READY_FOR_REVIEW
   │
   ▼
REVIEWING
   │
   ├──► CHANGES_REQUESTED
   │          │
   │          ▼
   │       WORKING
   │
   ▼
APPROVED
   │
   ▼
MERGING
   │
   ▼
DONE
```

---

# 16. Mapping mot Kanban

| Intern status | TeamPlayer |
|---|---|
| PLANNED | Planned |
| CLAIMED | Planned |
| STARTING | Planned |
| WORKING | Active |
| READY_FOR_REVIEW | Active |
| REVIEWING | Active |
| CHANGES_REQUESTED | Active |
| BLOCKED | Attention |
| PARKED | Attention |
| APPROVED | Active |
| MERGING | Active |
| DONE | Done |

Det gör Kanban enkelt samtidigt som Orchestratorn har tillräckligt detaljerad state.

---

# 17. Worker slots

Konfiguration:

```text
max_active_workers = 2
```

Scheduler:

```text
while epic has unfinished tasks:

    active = count(WORKING workers)

    if active < 2:
        find next runnable task

        if task exists:
            start worker
```

Tasks i `Attention` räknas inte som aktiv worker.

---

# 18. Dependency scheduling

Integration Agent ska bedöma vilka Tasks som kan köras parallellt.

Exempel:

```text
Task A ──► Task B

Task C
```

Scheduler kan starta:

```text
Worker 1 → Task A
Worker 2 → Task C
```

men inte Task B.

När Task A är mergad:

```text
Worker slot → Task B
```

Dependency betraktas som uppfyllt först efter merge till Epic-branchen.

---

# 19. Start av Worker

Flödet:

```text
Integration Agent
       │
       ▼
task_start(123)
       │
       ▼
Orchestrator
       │
       ├── validate task
       ├── create branch
       ├── create worktree
       ├── create Herdr workspace
       ├── start Codex
       ├── send Worker prompt
       └── persist runtime
              │
              ▼
        Worker reports WORKING
              │
              ▼
      Kanban → Active
```

---

# 20. Worker completion

Worker måste lämna ett maskinläsbart resultat.

Exempel:

```text
STATUS: READY_FOR_REVIEW

TASK_ID: 123

BRANCH:
task/123

COMMIT:
12a4f8e

TESTS:
PASS

SUMMARY:
Implemented ...

KNOWN_ISSUES:
None
```

Orchestratorn verifierar dessutom själv:

```text
branch contains commit

working tree clean

commit exists

tests reported
```

LLM-agentens rapport ska inte betraktas som enda sanningskälla.

---

# 21. Review-flöde

```text
READY_FOR_REVIEW
       │
       ▼
sync task with latest Epic
       │
       ▼
run tests
       │
       ▼
generate diff
       │
       ▼
Epic Integration Agent review
       │
       ├── CHANGES_REQUESTED
       │        │
       │        ▼
       │     Worker
       │
       └── APPROVED
                │
                ▼
          merge Task → Epic
```

---

# 22. Review-kontext

Integration Agent får:

```text
Task specification

Acceptance criteria

Task branch diff

Changed files

Test results

Relevant architecture documentation

Current Epic requirements
```

Agenten ska inte behöva försöka rekonstruera detta själv.

---

# 23. Attention-flöde

Worker:

```text
BLOCKED

Reason:
...

Input required:
...
```

Orchestrator:

```text
Task → Attention

Worker session → parked

active worker slot → released
```

Informationen skrivs till TeamPlayer-tasken.

När svar finns:

```text
Integration Agent
        │
        ▼
resume_task(task_id, answer)
        │
        ▼
same Codex session
        │
        ▼
Attention → Active
```

---

# 24. Epic completion

När inga Tasks återstår:

```text
all tasks Done
      │
      ▼
integration build
      │
      ▼
integration tests
      │
      ▼
Epic acceptance verification
      │
      ▼
EPIC_READY_FOR_REVIEW
```

Integration Agent skickar tillbaka kontrollen till Epic Coordinator.

---

# 25. Coordinator final review

Coordinatorn får:

```text
Epic requirements

Epic acceptance criteria

All task summaries

Epic branch diff vs main

Build results

Test results

Integration Agent report
```

Resultatet blir:

```text
EPIC_APPROVED
```

eller:

```text
EPIC_CHANGES_REQUESTED
```

---

# 26. Epic changes requested

Om Coordinator hittar problem återgår Epicen till Integration Agent.

Coordinatorn skickar exempelvis:

```text
EPIC_CHANGES_REQUESTED

1. Missing integration test for ...
2. Architecture requirement X is not satisfied.
```

Integration Agent kan då:

```text
create corrective task

eller

reopen existing task
```

och starta en Worker.

Epicen går sedan genom samma flöde igen.

---

# 27. Merge till main

Endast Epic Coordinator får initiera:

```text
feature/epic-* → main
```

Orchestratorn verifierar:

```text
all tasks Done

Epic review Approved

tests passed

main unchanged or synchronized

worktree clean
```

Därefter:

```text
merge --no-ff
```

och:

```text
Epic → Done
```

---

# 28. Recovery efter omstart

Recovery måste vara en förstaklassfunktion.

Vid uppstart läser Orchestratorn SQLite och jämför med:

```text
Git

Herdr

TeamPlayer Project
```

Exempel:

```text
Task 123:
SQLite       WORKING
Kanban       Active
branch       exists
worktree     exists
Herdr agent  exists
```

Resultat:

```text
resume monitoring
```

Om Codex-session saknas:

```text
resume existing Codex session
```

Om worktree saknas:

```text
Attention
```

i stället för att automatiskt gissa.

---

# 29. Locks

Systemet bör ha logiska locks.

Exempel:

```text
project lock

epic lock

task lock
```

Det ska aldrig gå att:

```text
starta två Integration Agents för samma Epic

eller

starta samma Task två gånger
```

---

# 30. Idempotens

Alla Orchestrator-operationer ska vara idempotenta där det är möjligt.

Om:

```text
task_start(123)
```

anropas två gånger ska andra anropet exempelvis svara:

```text
Task 123 already running

worker:
agent-123

worktree:
/home/.../task-123
```

inte skapa ytterligare branch och Worker.

---

# 31. Agent policies

Tre separata systemprompts/policies ska skapas.

## Coordinator policy

Definierar:

```text
owns main
selects epics
final reviews
may merge epic → main
```

## Integration policy

Definierar:

```text
owns Epic
manages Workers
reviews Tasks
may merge task → epic
```

## Worker policy

Definierar:

```text
owns one Task
implements
tests
commits
never merges
```

---

# 32. Observability

Varje operation ska loggas.

Exempel:

```text
14:03:21 Epic 123 claimed
14:03:23 Epic worktree created
14:03:26 Integration Agent started

14:04:05 Task 456 assigned to Worker 1
14:04:08 Task 457 assigned to Worker 2

14:31:12 Task 456 READY_FOR_REVIEW
14:34:41 Task 456 APPROVED
14:34:48 Task 456 merged

14:40:22 Task 457 BLOCKED
14:40:24 Task 457 → Attention
```

---

# 33. Audit log

Separera gärna runtime-logg och audit-logg.

Audit-loggen ska innehålla viktiga beslut:

```text
Epic selected

Task assigned

Attention reason

Review result

Merge SHA

Epic approval

main merge SHA
```

Det gör det möjligt att i efterhand förstå exakt vad agentsystemet gjorde.

---

# 34. Projektstruktur

Första implementationen kan exempelvis få följande struktur:

```text
HerdrOrchestrator/
│
├── src/
│   └── orchestrator/
│       ├── __init__.py
│       │
│       ├── application/
│       │   ├── coordinator_service.py
│       │   ├── epic_service.py
│       │   ├── task_service.py
│       │   └── scheduler.py
│       │
│       ├── domain/
│       │   ├── epic.py
│       │   ├── task.py
│       │   ├── agent.py
│       │   ├── states.py
│       │   └── policies.py
│       │
│       ├── adapters/
│       │   ├── git_adapter.py
│       │   ├── herdr_adapter.py
│       │   ├── teamplayer_adapter.py
│       │   └── codex_adapter.py
│       │
│       ├── persistence/
│       │   ├── database.py
│       │   └── repositories.py
│       │
│       └── mcp/
│           ├── server.py
│           └── tools.py
│
├── prompts/
│   ├── coordinator.md
│   ├── integration.md
│   └── worker.md
│
├── tests/
│
├── orchestrator.db
└── pyproject.toml
```

---

# 35. Implementation Phase 1 – Foundation

Målet är att skapa teknisk grund utan AI-automation.

Implementera:

```text
Python project

configuration

SQLite

logging

domain models

Task/Epic state enums
```

Definition of Done:

```text
Orchestrator can start

database created

EpicRun / TaskRun persisted

state transitions tested
```

---

# 36. Phase 2 – Git automation

Implementera Git Adapter.

Funktioner:

```text
create_epic_worktree()

create_task_worktree()

remove_worktree()

sync_task_with_epic()

merge_task()

merge_epic()

get_diff()

get_current_commit()
```

Tester ska använda temporära Git repositories.

Definition of Done:

```text
Epic + two parallel Task worktrees
can be created and merged automatically.
```

---

# 37. Phase 3 – Herdr automation

Implementera Herdr Adapter.

Först:

```text
create workspace

start Codex

send prompt

detect status

resume Codex

stop worker
```

Definition of Done:

Orchestratorn ska kunna skapa:

```text
Epic worktree
      │
      ▼
Herdr workspace
      │
      ▼
Codex
```

utan manuell terminalinteraktion.

---

# 38. Phase 4 – Worker MVP

Implementera endast en Worker.

Flöde:

```text
manually provide Task
      │
      ▼
create Task worktree
      │
      ▼
start Codex
      │
      ▼
send Worker prompt
      │
      ▼
Worker implementation
      │
      ▼
commit
      │
      ▼
READY_FOR_REVIEW
```

Ingen automatisk merge ännu.

Detta validerar Codex/Herdr-kommunikationen.

---

# 39. Phase 5 – Review loop

Implementera:

```text
READY_FOR_REVIEW

Integration Agent review

CHANGES_REQUESTED

Worker resume

APPROVED
```

När Approved:

```text
Task → Epic merge
```

Definition of Done:

En Task kan gå automatiskt från:

```text
start → implementation → review → correction → merge
```

---

# 40. Phase 6 – TeamPlayer MCP

Koppla in TeamPlayer Project.

Implementera:

```text
get Epic

get Tasks

set Active

set Attention

set Done

write Attention reason
```

Testa först med en test-Epic.

Ovanstående Attention gäller tasks. Epics har Planned/Active/Done: verifiera separat epicstart, fortsatt Active när tasks är blockerade eller klara före main-integration, och Done först efter hela epicens leveransgrind. Coordinator initierar epicstatus och Integration taskstatus. Både task- och epicversion ska återläsas före respektive skrivning.

---

# 41. Phase 7 – Två parallella Workers

Aktivera:

```text
max_active_workers = 2
```

Scheduler startar:

```text
Task 1 → Worker 1

Task 2 → Worker 2
```

När en blir klar:

```text
Task 3 → ledig slot
```

Definition of Done:

Minst tre Tasks ska kunna genomföras med två Workers utan manuell scheduling.

---

# 42. Phase 8 – Attention

Implementera:

```text
BLOCKED

park session

Kanban Attention

release Worker slot

receive answer

resume same Codex session

Kanban Active
```

Definition of Done:

En blockerad Task ska inte stoppa andra Tasks.

---

# 43. Phase 9 – Epic Integration Agent

Flytta task scheduling och review till en långlivad Integration Agent.

Flödet blir:

```text
Epic start

Integration Agent start

Workers managed automatically

all Tasks integrated

Epic verification

EPIC_READY_FOR_REVIEW
```

---

# 44. Phase 10 – Epic Coordinator

Implementera hela ytterloopen:

```text
get next Epic

start Epic

wait

final review

merge main

get next Epic
```

Efter detta finns första kompletta autonoma versionen.

---

# 45. Phase 11 – Recovery

Testa avsiktliga avbrott:

```text
kill Orchestrator

disconnect SSH

restart Herdr

restart Pi

terminate Codex
```

Systemet ska kunna identifiera tidigare state och återuppta arbetet.

---

# 46. Phase 12 – Hardening

Lägg till:

```text
locks

timeouts

retry policies

idempotency

branch validation

merge guards

clean worktree validation

structured errors

audit logging
```

---

# 47. MVP-avgränsning

Första MVP:n bör inte försöka göra allt.

Jag föreslår:

```text
1 repository

1 Epic

2 Workers

TeamPlayer Kanban

Planned / Active / Attention / Done

Task review

Task merge

Epic review

Epic merge
```

Undvik initialt:

```text
flera Epics samtidigt

fler än två Workers

cross-repository Tasks

automatisk conflict resolution på Epic-nivå

dynamisk agent scaling
```

---

# 48. Första end-to-end-scenario

Första riktiga testet bör vara en liten Epic med tre Tasks.

Exempel:

```text
Epic 100
│
├── Task 101
├── Task 102
└── Task 103
```

Systemet ska göra:

```text
Coordinator
   │
   ▼
Epic 100 Active
   │
   ▼
Integration Agent
   │
   ├── Worker 1 → Task 101 → Active
   └── Worker 2 → Task 102 → Active
                     │
Worker 1 done        │
   │                 │
review               │
   │                 │
merge                │
   │                 │
Task 101 Done        │
   │                 │
   ▼                 │
Worker 1 → Task 103  │
                     │
                     ▼
                  review
                     │
                    Done

        all Tasks Done
              │
              ▼
        Epic verification
              │
              ▼
       Coordinator review
              │
              ▼
        merge → main
              │
              ▼
         Epic 100 Done
```

Om detta scenario fungerar robust finns kärnan i systemet.

---

# 49. Rekommenderad implementationsteknik

För Orchestratorn:

```text
Python 3.12+

SQLite

asyncio

Pydantic

SQLAlchemy eller sqlite3

MCP Python SDK

subprocess/asyncio subprocess för Git och Herdr
```

Jag skulle hålla första implementationen relativt lätt och undvika ett tungt workflow-framework.

State machine och scheduling är tillräckligt enkla för att implementeras explicit.

---

# 50. Arkitekturell huvudregel

Den viktigaste separationen är:

```text
LLM
decides what should happen

        │
        ▼

Orchestrator
decides whether it is allowed
and performs it deterministically

        │
        ▼

Git / Herdr / TeamPlayer
```

Det innebär att en hallucinerande eller felinstruerad Worker inte kan mergea till `main` bara för att den försöker.

---

# 51. Slutlig målbild

```text
                       TeamPlayer Project
                              │
                              ▼
                     EPIC COORDINATOR
                           Codex
                              │
                              ▼
                     Agent Orchestrator
                 Python + MCP + SQLite
                              │
               ┌──────────────┼──────────────┐
               │              │              │
               ▼              ▼              ▼
              Git           Herdr        TeamPlayer
               │              │              MCP
               │              │
               │              ▼
               │      EPIC INTEGRATION AGENT
               │              │
               │        ┌─────┴─────┐
               │        │           │
               │        ▼           ▼
               │     WORKER 1    WORKER 2
               │       Codex       Codex
               │        │           │
               │        ▼           ▼
               │      Task A      Task B
               │        │           │
               └────────┴────┬──────┘
                             │
                             ▼
                        Epic branch
                             │
                     Coordinator review
                             │
                             ▼
                            main
```

Detta ger ett system där **Codex står för analys, implementation och review**, medan **Herdr står för agentruntime**, **Git worktrees står för isolering**, **TeamPlayer Project står för arbetsstatus** och **Orchestratorn garanterar att processen genomförs på ett kontrollerat och återstartningsbart sätt**.
