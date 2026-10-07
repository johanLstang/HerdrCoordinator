# Arbetsprocess för Herdr-baserat multi-agentflöde

## Utveckling av HerdrCoordinator

När HerdrCoordinator byggs följer arbetet [Utvecklingsprocess.md](Utvecklingsprocess.md) och [AGENTS.md](AGENTS.md): en feature/task i taget, TeamPlayer-projektet HerdrCoordinator för aktuell status och tilldelning samt Backlog.md för scope, ordning och acceptans. Implementera i task-worktree, integrera granskad task till epic och slutgranskad epic via PR till main. Under bootstrap utför ansvarig utvecklare rollerna sekventiellt.

Det parallella agentflödet nedan beskriver produktens beteende. Produktens två Worker-slots ändrar inte ordningen för den manuella utvecklingen av projektets features.

## 1. Syfte

Syftet med detta workflow är att automatisera utvecklingen av Epics och Tasks med flera parallella Codex-agenter via Herdr.

Arbetsmodellen bygger på tre agentnivåer:

- **Epic Coordinator** – ansvarar för projektets Epics och merge till `main`.
- **Epic Integration Agent** – ansvarar för genomförandet av en enskild Epic.
- **Worker Agents** – implementerar individuella Tasks parallellt.

Git branches och Git worktrees används för att isolera varje agents arbete.

TeamPlayer-projektets Kanban-tavla är systemets primära källa för arbetsstatus.

---

# 2. Övergripande arkitektur

```text
                       TeamPlayer Project
                             │
                             ▼
                    ┌───────────────────┐
                    │ Epic Coordinator  │
                    │      Codex        │
                    └─────────┬─────────┘
                              │
                      väljer nästa Epic
                              │
                              ▼
                    feature/epic-123
                              │
                    Epic worktree
                              │
                              ▼
               ┌─────────────────────────┐
               │ Epic Integration Agent  │
               │          Codex          │
               └────────────┬────────────┘
                            │
                 väljer och startar Tasks
                            │
                ┌───────────┴───────────┐
                │                       │
                ▼                       ▼
         task/123-1               task/123-2
         worktree                  worktree
                │                       │
                ▼                       ▼
          Worker Agent 1           Worker Agent 2
              Codex                    Codex
                │                       │
                └──────────┬────────────┘
                           │
                    review + merge
                           │
                           ▼
                    feature/epic-123
                           │
                     slutlig review
                           │
                           ▼
                          main
```

---

# 3. Git-struktur

Varje Epic får en egen feature-branch:

```text
main
 └── feature/epic-123
```

Tasks inom Epicen får egna branches:

```text
main
 └── feature/epic-123
      ├── task/123-1
      ├── task/123-2
      ├── task/123-3
      └── task/123-4
```

Varje aktiv branch arbetar i ett separat Git worktree.

Exempel:

```text
~/projects/Anemoria/
    main

~/worktrees/Anemoria-epic-123/
    feature/epic-123

~/worktrees/Anemoria-task-123-1/
    task/123-1

~/worktrees/Anemoria-task-123-2/
    task/123-2
```

Ingen Worker Agent delar arbetskatalog med någon annan agent.

---

# 4. Ägarskap

En viktig princip är att varje agentnivå endast äger sin del av Git-strukturen.

```text
Epic Coordinator
    owns:
        main

Epic Integration Agent
    owns:
        feature/epic-*

Worker Agent
    owns:
        task/*
```

Detta innebär:

- Worker Agents får aldrig mergea till Epic-branchen.
- Epic Integration Agent får aldrig mergea Epicen till `main`.
- Epic Coordinator ansvarar ensam för merge till `main`.

---

# 5. Epic Coordinator

Epic Coordinator är persistent över hela projektet.

Dess ansvar är att:

1. läsa TeamPlayer-projektet via MCP,
2. identifiera nästa prioriterade obehandlade Epic,
3. skapa Epic-branch,
4. skapa Epic-worktree,
5. starta Epic Integration Agent,
6. överlämna Epicens kontext,
7. ta emot färdig Epic från Integration Agent,
8. genomföra slutlig Epic-review,
9. mergea godkänd Epic till `main`,
10. markera Epic som `Done`,
11. välja nästa Epic.

Epic Coordinator arbetar inte normalt med implementation av enskilda Tasks.

---

# 6. Start av en ny Epic

När en Epic väljs:

```text
Epic status:
Planned → Active
```

Coordinatorn skapar:

```text
feature/epic-123
```

och ett separat Epic-worktree.

Exempel:

```bash
git worktree add \
    ../Anemoria-epic-123 \
    -b feature/epic-123 \
    main
```

Därefter startas en ny Codex-session i Epic-worktreet.

Den sessionen blir:

```text
Epic Integration Agent
```

Integration Agent får:

- Epic-ID
- titel
- beskrivning
- Epic acceptance criteria
- tillhörande Tasks
- prioriteringar
- beroenden
- projektspecifika instruktioner

---

# 7. Epic Integration Agent

Epic Integration Agent lever under hela Epicens livscykel.

Agenten ansvarar för:

- task-planering,
- task-prioritering,
- beroenden,
- skapande av Task branches,
- skapande av worktrees,
- start av Worker Agents,
- övervakning av Workers,
- task-review,
- feedbackloopar,
- merge från Task till Epic,
- integrationstester,
- rapportering tillbaka till Epic Coordinator.

Integration Agent ska normalt inte implementera Tasks själv.

---

# 8. Worker slots

Systemet har initialt två Worker slots:

```text
Worker Slot 1
Worker Slot 2
```

Det betyder:

> Maximalt två Worker Agents arbetar aktivt samtidigt.

En Worker Agent är inte permanent.

Dess livslängd motsvarar normalt en Task:

```text
Start Task
   ↓
Codex-session skapas
   ↓
Implementation
   ↓
Review
   ↓
Merge
   ↓
Codex-session avslutas
```

Nästa Task får normalt en ny Codex-session.

Det minskar risken att kontext från tidigare Tasks påverkar nästa arbete.

---

# 9. Val av Tasks

Epic Integration Agent hämtar Epicens Tasks från TeamPlayer Project.

Tasks väljs baserat på:

1. status,
2. prioritet,
3. beroenden,
4. om de kan köras parallellt.

Agenten väljer upp till två körbara Tasks.

Exempel:

```text
Task 101   Priority 1   Planned
Task 102   Priority 2   Planned
Task 103   Priority 3   Planned
```

Task 101 tilldelas Worker Slot 1.

Task 102 tilldelas Worker Slot 2.

---

# 10. Start av en Task

När en Task tilldelas:

1. Task-branch skapas från aktuell Epic-branch.
2. Task-worktree skapas.
3. Codex Worker Agent startas i worktreet.
4. Worker Agent får sitt uppdrag.
5. Worker bekräftar att arbetet har startat.
6. Taskens Kanban-status ändras till `Active`.

Exempel:

```text
feature/epic-123
        │
        └── task/123-1
```

Worktree:

```text
~/worktrees/Anemoria-task-123-1
```

---

# 11. Worker Agent – uppdrag

Worker Agent får ett standardiserat uppdrag.

Exempel:

```text
Task ID: 123-1
Epic ID: 123

Branch:
task/123-1

Base branch:
feature/epic-123

Goal:
Implementera funktionen som beskrivs i Task 123-1.

Acceptance criteria:
- ...
- ...
- ...

Rules:
- Arbeta endast med denna Task.
- Arbeta endast i tilldelat worktree.
- Byt inte branch.
- Mergea aldrig.
- Modifiera inte Epic-branch eller main.
- Kör relevanta tester.
- Committera allt färdigt arbete.
- Rapportera resultatet när arbetet är klart.
```

---

# 12. Worker Agent – tillåtna operationer

Worker Agent får:

- läsa projektet,
- modifiera filer,
- skapa filer,
- köra tester,
- köra builds,
- skapa commits på sin Task-branch,
- rapportera frågor och blockers.

Worker Agent får inte:

- mergea branches,
- byta till Epic-branch,
- byta till `main`,
- modifiera andra worktrees,
- starta andra Tasks,
- ändra Kanban-status direkt.

---

# 13. Task-status i TeamPlayer Project

Kanban-tavlan använder följande huvudsakliga Task-statusar:

```text
Planned
Active
Attention
Done
```

Den normala livscykeln är:

```text
Planned
   │
   ▼
Active
   │
   ├──────────────► Attention
   │                    │
   │                    │ problem löst
   │                    ▼
   │◄────────────── Active
   │
   ▼
Done
```

---

# 14. Planned

`Planned` betyder:

- Tasken finns,
- den är inte färdig,
- ingen Worker arbetar för närvarande med den.

En Task kan även ligga kvar i `Planned` om den väntar på ett beroende.

---

# 15. Active

`Active` betyder:

> En Worker arbetar aktivt med Tasken eller Tasken befinner sig i review/fix-loop.

Status sätts först när:

1. worktree skapats,
2. Worker Agent startats,
3. Task-prompt skickats,
4. Worker bekräftat att arbetet påbörjats.

---

# 16. Attention

`Attention` används när automationen inte kan fortsätta utan extern input eller åtgärd.

Exempel:

- otydligt krav,
- arkitekturbeslut krävs,
- saknad information,
- blockerande tekniskt problem,
- credentials eller extern resurs saknas,
- motstridiga acceptance criteria.

Worker rapporterar exempelvis:

```text
STATUS: BLOCKED

REASON:
Specification does not define how deleted locations
should be handled.

INPUT_REQUIRED:
Should deleted locations remain available in
historical timelines?
```

Epic Integration Agent:

1. parkerar Worker-sessionen,
2. ändrar Task-status till `Attention`,
3. registrerar orsaken på Tasken.

---

# 17. Återuppta Attention

När problemet är löst skickas beslutet tillbaka till samma Worker-session.

Exempel:

```text
Blocking question resolved:

Deleted locations must remain available for
historical timelines.

Resume implementation using this decision.
```

När Worker återupptar arbetet:

```text
Attention → Active
```

Tasken fortsätter i samma branch och worktree.

---

# 18. Attention blockerar inte hela Epicen

En blockerad Worker behöver inte konsumera en aktiv Worker slot.

Exempel:

```text
Worker Slot 1
    Task 101 Active

Worker Slot 2
    Task 102 Attention
        session parked

Worker Slot 2
    Task 103 Active
        new Worker session
```

Detta innebär att maximalt två Workers arbetar aktivt samtidigt, men flera parkerade sessioner kan existera.

När Task 102 får sitt svar återupptas den när en Worker slot blir tillgänglig.

---

# 19. Worker färdigställer Task

När implementationen är klar ska Worker:

1. köra tester,
2. kontrollera Git-status,
3. committa alla relevanta ändringar,
4. rapportera resultatet.

Exempel:

```text
STATUS: READY_FOR_REVIEW

TASK:
123-1

BRANCH:
task/123-1

COMMIT:
a817f92

SUMMARY:
Implemented dungeon entrance generation.

TESTS:
142 passed
0 failed

FILES_CHANGED:
- DungeonGenerator.cs
- DungeonGeneratorTests.cs

KNOWN_ISSUES:
None
```

Taskens Kanban-status är fortfarande:

```text
Active
```

Den är ännu inte `Done`.

---

# 20. Task Review

Epic Integration Agent ansvarar för review.

Reviewen omfattar minst:

- acceptance criteria,
- funktionell korrekthet,
- kodkvalitet,
- arkitektur,
- scope,
- oavsiktliga ändringar,
- tester,
- regressioner,
- dokumentation.

Integration Agent granskar skillnaden mellan:

```text
feature/epic-123
```

och:

```text
task/123-1
```

---

# 21. Changes Requested

Om reviewen hittar problem returneras:

```text
CHANGES_REQUESTED
```

Exempel:

```text
REVIEW: CHANGES_REQUESTED

1. Missing validation for invalid location ID.
2. Add a regression test.
3. Method naming does not follow project conventions.
```

Feedback skickas tillbaka till samma Worker-session.

Tasken behåller:

```text
Active
```

Worker gör korrigeringarna och committar nya ändringar.

Sedan rapporteras åter:

```text
READY_FOR_REVIEW
```

Review-loopen upprepas tills Tasken är godkänd.

---

# 22. Review som kräver extern input

Om reviewen identifierar ett problem som Integration Agent eller Worker inte själv bör avgöra:

```text
Active → Attention
```

Exempel:

```text
Architecture decision required:

Should the new history storage reuse the existing
timeline model or introduce a separate persistence model?
```

När beslut fattats återgår Tasken till:

```text
Active
```

---

# 23. Synkronisering mot aktuell Epic

Två Tasks kan starta från samma version av Epic-branchen.

Exempel:

```text
feature/epic-123
      ├── task-A
      └── task-B
```

Om Task A blir färdig först och mergeas:

```text
feature/epic-123
      + Task A
```

arbetar Task B fortfarande från en äldre version.

Innan Task B slutgodkänns ska den därför synkroniseras mot aktuell Epic.

Normalt:

```bash
git merge feature/epic-123
```

in i Task B.

Eventuella konflikter löses i Task-worktreet.

Tester körs igen.

Därefter sker slutlig review.

Regeln är:

> En Task får inte slutgodkännas mot en inaktuell version av Epic-branchen.

---

# 24. Godkänd Task

När reviewen är godkänd:

```text
APPROVED
```

Epic Integration Agent mergear Task-branchen till Epic-branchen.

Exempel:

```bash
git merge --no-ff task/123-1
```

Efter merge körs relevanta integrationstester.

Om merge och tester lyckas:

```text
Task status → Done
```

---

# 25. Definition av Done

En Task får endast markeras som `Done` när:

- implementationen är färdig,
- ändringarna är committade,
- Worker-tester är godkända,
- Epic Integration Agent har genomfört review,
- reviewen är godkänd,
- Task-branchen är mergad till Epic-branchen,
- relevanta integrationstester är godkända.

`Done` betyder därför:

> Tasken är integrerad i Epicen.

Inte:

> Worker Agent tycker att implementationen är klar.

---

# 26. Cleanup efter Task

Efter godkänd merge kan Task-worktreet tas bort.

Exempel:

```bash
git worktree remove ../Anemoria-task-123-1
```

Task-branchen kan tas bort enligt projektets branch-policy.

Worker-sessionen avslutas.

Worker-slotten blir ledig.

---

# 27. Kontinuerlig Task-scheduling

Epic Integration Agent försöker hålla två Workers aktiva så länge körbara Tasks finns.

Exempel:

```text
Worker 1
Task 101
   ↓
Done
   ↓
Task 103

Worker 2
Task 102
   ↓
fortsätter
```

När en Worker blir ledig väljs nästa:

- `Planned`
- högst prioriterade
- icke blockerade
- beroendemässigt körbara

Task.

---

# 28. Task-beroenden

Integration Agent måste ta hänsyn till Task-beroenden.

Exempel:

```text
Task A
  ↓
Task B

Task C
```

Task A och Task C kan köras parallellt.

Task B får inte starta innan Task A har:

```text
reviewats
+
mergats till Epic
```

Det räcker inte att Task A:s Worker säger att arbetet är klart.

---

# 29. När alla Tasks är klara

När alla Epicens Tasks är `Done` gör Epic Integration Agent en samlad integrationskontroll.

Den omfattar:

- build,
- test suite,
- Epic acceptance criteria,
- integrationsproblem mellan Tasks,
- arkitektur,
- dokumentation,
- eventuella regressioner.

Epic Integration Agent får fortfarande inte mergea till `main`.

---

# 30. Överlämning till Epic Coordinator

När Integration Agent bedömer Epicen som färdig skickas:

```text
EPIC_READY_FOR_REVIEW
```

Exempel:

```text
EPIC_READY_FOR_REVIEW

Epic:
123

Branch:
feature/epic-123

Tasks:
123-1 DONE
123-2 DONE
123-3 DONE
123-4 DONE

Integration tests:
PASS

Epic acceptance criteria:
PASS

Known issues:
None
```

---

# 31. Epic Coordinator – slutreview

Epic Coordinator gör en samlad review av hela Epicen.

Denna review ligger på en högre nivå än Task-review.

Coordinatorn granskar:

- Epic acceptance criteria,
- integrationen mellan Tasks,
- arkitekturella konsekvenser,
- projektets övergripande design,
- regressioner,
- testresultat,
- dokumentation,
- scope creep,
- kompatibilitet med `main`.

---

# 32. Epic Changes Requested

Om slutreviewen inte godkänns:

```text
EPIC_CHANGES_REQUESTED
```

Epicen lämnas tillbaka till Epic Integration Agent.

Integration Agent kan:

- själv analysera problemet,
- skapa en ny Task,
- återöppna en tidigare Task,
- starta en ny Worker,
- genomföra ny review och integration.

Epicen återlämnas därefter till Coordinatorn.

---

# 33. Epic Approved

När Coordinatorns slutreview är godkänd:

```text
EPIC_APPROVED
```

Coordinatorn mergear Epic-branchen till `main`.

Exempel:

```bash
git switch main

git merge --no-ff feature/epic-123
```

Därefter körs projektets slutliga verifiering.

Vid lyckat resultat:

```text
Epic status → Done
```

---

# 34. Nästa Epic

Epic Coordinator frågar TeamPlayer Project efter nästa:

```text
Planned
+
prioriterade
+
körbara
```

Epic.

Om en sådan finns startas processen om:

```text
Epic Coordinator
      │
      ▼
Next Epic
      │
      ▼
Epic worktree
      │
      ▼
Epic Integration Agent
      │
 ┌────┴────┐
 ▼         ▼
Worker 1  Worker 2
```

Processen fortsätter tills inga körbara Epics återstår.

---

# 35. Interna tillstånd

Kanban-tavlan behöver endast visa ett begränsat antal statusar.

## Task

```text
Planned
Active
Attention
Done
```

Internt kan orchestratorn använda mer detaljerade tillstånd:

```text
PLANNED
ASSIGNING
STARTING
WORKING
BLOCKED
READY_FOR_REVIEW
REVIEWING
CHANGES_REQUESTED
APPROVED
MERGING
DONE
```

---

# 36. Task state machine

```text
                  ┌─────────────┐
                  │   Planned   │
                  └──────┬──────┘
                         │
                       assign
                         │
                         ▼
                  ┌─────────────┐
                  │   Active    │
                  └──────┬──────┘
                         │
           ┌─────────────┼──────────────┐
           │             │              │
         blocked     work complete    continue
           │             │
           ▼             ▼
     ┌───────────┐  READY_FOR_REVIEW
     │ Attention │        │
     └─────┬─────┘        ▼
           │           Review
       resolved           │
           │        ┌─────┴─────┐
           │        │           │
           │     changes     approved
           │     requested       │
           │        │            ▼
           └────────┴──────►   Merge
                               │
                               ▼
                         ┌───────────┐
                         │   Done    │
                         └───────────┘
```

---

# 37. Epic state machine

```text
Planned
   │
   ▼
Active
   │
   ▼
Tasks executing
   │
   ▼
Integration complete
   │
   ▼
Final review
   │
   ├── Changes requested
   │        │
   │        ▼
   │      Active
   │
   └── Approved
           │
           ▼
         Merge
           │
           ▼
          Done
```

---

# 38. Agentlivslängd

## Epic Coordinator

```text
Persistent över projektet
```

Kan hantera många Epics under samma livslängd.

## Epic Integration Agent

```text
Persistent över en Epic
```

Lever från att Epicen startas tills den är godkänd av Coordinatorn.

## Worker Agent

```text
Persistent över en Task
```

Lever normalt endast så länge Tasken implementeras och reviewas.

---

# 39. Ansvar för Kanban-status

Worker Agents ändrar inte TeamPlayer Project direkt.

De rapporterar endast sina tillstånd.

Exempel:

```text
WORKING
BLOCKED
READY_FOR_REVIEW
```

Epic Integration Agent översätter detta till Kanban-status:

```text
WORKING
→ Active

BLOCKED
→ Attention

MERGED
→ Done
```

Epic Coordinator ansvarar för Epic-status.

Epics använder endast `Planned`, `Active`, `Done`:

- Planned: epicen är inte påbörjad; väntan på beroenden räknas inte som start.
- Active: Coordinator har startat epicen, senast när första tasken plockas. Statusen består under implementation, review, korrigering, paus, hinder och väntan på main-integration/slutverifiering.
- Done: alla tasks är Done och samlad acceptans, slutreview, main-merge samt slutverifiering är genomförda.

En task kan vara Attention medan epicen är Active. Blockerare, ansvarig roll och nästa åtgärd dokumenteras separat; epics får inte Attention och en påbörjad epic återgår inte till Planned vid hinder. Alla tasks Done lämnar epicen Active tills leveransgrinden är uppfylld. Nytt arbete i en Done-epic kräver dokumenterat återöppningsbeslut och Active.

Coordinator läser board-epicens status/version med `list_epics`, skriver med `update_epic_status` och färsk epicversion, och återläser före uppdatering av backloggen. TeamPlayer API mappar Pending → Planned, InProgress → Active, Done → Done. Versionskonflikt eller okänt nätutfall kräver återläsning före retry. Vid väntande extern synk sparas faktisk leverans och synkavsikt; Git-merge upprepas inte. Interna EpicRun-faser mellan PLANNED och verifierad DONE motsvarar Active på boarden.

Detta innebär att endast en agentnivå skriver status för respektive nivå.

---

# 40. Grundläggande säkerhetsregler

Följande regler gäller alltid:

1. En Worker får endast skriva i sitt eget worktree.
2. En Worker får aldrig mergea.
3. En Worker får aldrig arbeta direkt på Epic-branchen.
4. Epic Integration Agent får endast mergea Task → Epic.
5. Epic Coordinator får endast mergea Epic → main.
6. Ingen branch mergeas utan review.
7. Ingen Task sätts till `Done` innan den är mergad.
8. Ingen Epic sätts till `Done` innan den är mergad till `main`.
9. Blockerad automation resulterar i `Attention`, inte gissningar.
10. Alla aktiva Tasks måste kunna härledas till en Epic.

---

# 41. Sammanfattat workflow

```text
Epic Coordinator
        │
        │ MCP
        ▼
TeamPlayer Project
        │
        │ nästa Epic
        ▼
Create Epic branch/worktree
        │
        ▼
Start Epic Integration Agent
        │
        ├─────────────────────────────┐
        │                             │
        ▼                             ▼
Select Task 1                   Select Task 2
        │                             │
Create worktree                Create worktree
        │                             │
        ▼                             ▼
Start Worker 1                 Start Worker 2
        │                             │
Kanban → Active                Kanban → Active
        │                             │
        ▼                             ▼
Implement                      Implement
        │                             │
        ├─ blocked                    ├─ blocked
        │    ↓                        │    ↓
        │ Attention                   │ Attention
        │                             │
        ▼                             ▼
Commit                         Commit
        │                             │
READY_FOR_REVIEW              READY_FOR_REVIEW
        │                             │
        └──────────────┬──────────────┘
                       ▼
             Epic Integration Review
                       │
              ┌────────┴────────┐
              │                 │
        Changes requested    Approved
              │                 │
              ▼                 ▼
            Worker         Merge Task → Epic
                                │
                                ▼
                          Kanban → Done
                                │
                                ▼
                         Next Planned Task
                                │
                                ▼
                       All Tasks Done?
                           │         │
                          No        Yes
                           │         │
                           └───┐     ▼
                               │ Integration test
                               │     │
                               │     ▼
                               │ EPIC_READY_FOR_REVIEW
                               │     │
                               │     ▼
                               │ Epic Coordinator
                               │     │
                               │ Final review
                               │     │
                               │ ┌───┴─────────┐
                               │ │             │
                               │ Changes     Approved
                               │ │             │
                               │ ▼             ▼
                               └ Integration  Merge → main
                                  Agent          │
                                                 ▼
                                             Epic Done
                                                 │
                                                 ▼
                                             Next Epic
```

---

# 42. Kärnprincip

Det fullständiga arbetsflödet kan sammanfattas med:

```text
Epic Coordinator
    owns main

Epic Integration Agent
    owns feature/epic

Worker Agent
    owns task
```

samt:

```text
Worker implements
        ↓
Integration Agent reviews and integrates
        ↓
Coordinator validates and releases
```

Det ger tydliga ansvarsnivåer, isolerade arbetsytor, kontrollerade merges och ett Kanban-system som alltid speglar det verkliga utvecklingsläget.
