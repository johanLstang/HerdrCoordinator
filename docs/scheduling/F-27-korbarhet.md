# F27: välj nästa körbara task

`TaskSelectionService` gör ett läsande urval för en registrerad Integration-principal
inom en aktiv produkt-EpicRun. `get_next(actor, epic_run_id)` läser en ny, komplett
TeamPlayer-board genom F24:s `TeamPlayerReader` och returnerar nästa task, alla
körbara kandidater och blockerare för varje task i epicen.

## Betrodd konfiguration

Operatören registrerar följande vid konstruktion av tjänsten:

- `reader`: autentiserad F24-läsare med explicit UUID → `LocalBoardBinding`.
- `specs`: externa task-UUID:n → fullständiga version-1-`LocalTaskSpec`, även för
  beroenden i tidigare epics. Prosa och titlar parsas aldrig till exekverbar scope.
- `task_order`: varje registrerad task-UUID exakt en gång, i backloggens stabila
  ordning. Native prioritet sorteras först, högst tal först; lika prioritet följer
  denna ordning. Ett oregistrerat objekt får blockerare, inte en uppfunnen spec.
- `epic_prerequisites`: lokal epicidentitet → externa epic-UUID:n som ska vara
  levererade till main. En tom lista måste också anges uttryckligen för en epic
  utan sådana krav. Cross-epic-taskberoenden kräver alltid epicens mainbevis,
  även om epicen inte nämns i denna lista.
- `delivery_contexts`: tidigare EpicRun-ID → dess operatörskonfigurerade `Settings`,
  inklusive egen epicacceptans. Repository och SQLite måste vara samma som för
  aktuell produkt. `scopes` binder dessa EpicRun-ID:n till fullständig lokal taskscope.
- Valfri `prerequisite_probe(spec)` ska vara en betrodd **läsande** kontroll av
  samtliga angivna externa förutsättningar. Endast exakt `True` godtas. Saknad probe,
  annat svar och undantag ger `TASK_PREREQUISITE_UNVERIFIED`; inga råa fel återges.

Specen ska matcha lokal project/epic/task, boardens namn, acceptans och uttryckligt
mappade beroenden. Blank/saknad/ogiltig eller för stor specifikation spärrar urvalet.
Källor, requirements, scope, avgränsningar och verifieringssteg är obligatoriska
F14-fält. Operatören ansvarar för innehållet och att hålla det synkat med backloggen.

## Körbarhet och bevis

En kandidat måste vara native `Pending`/lokalt Planned, ha korrekt User-tilldelning,
komplett specifikation, uppfyllda externa krav och ingen tidigare TaskRun-ägare.
En separat redan skapad PLANNED-run adopteras inte. En upptagen taskbranch eller
worktree utan motsvarande run ger också blockerare och adopteras inte. Pågående eget arbete återupptas
med dess registrerade run; detta verktyg föreslår nya tasks.

F24:s grafkontroller bevaras och beräknas också vid direkt `evaluate`:
cykler, okända/duplicerade beroenden, Attention, Cancelled, ofullständiga eller
felaktiga förfäder spärrar berörda kandidater. Faktiskt leveransbevis verifieras för
hela beroendekedjan, även när alla förfäder har boardstatus Done. En oberoende frisk task kan väljas
även när en annan del av grafen väntar.

Taskberoenden kräver både native Done och verklig lokal DONE. F25:s läsande
`TeamPlayerEvidence` kontrollerar F15-spec/journal, senaste Done-händelse, F20-approval,
F18-kontext, aktuella task-/review-SHA, registrerad F07-merge med exakta två parents
och operationstag, inkludering i aktuell epicbranch, F21:s godkända integrationstest
på exakt merge-SHA samt registrerat stopp och faktisk processinaktivitet. Kanban Done,
READY_FOR_REVIEW, APPROVED eller en ensam mergecommit räcker inte.

Tidigare epics kräver därutöver fullständig native/lokal taskscope, egen godkänd
acceptans, aktuell slutreview/manifest, F08:s verkliga Epic → main-merge och godkänt
sluttest. Mergecommiten ska fortfarande finnas i både main och aktuell epicbranch;
en epicbranch från main före beroendets leverans spärras.

Git-integrationslåset och en SQLite-transaktion håller serviceoperationernas lokala
observation sammanhängande. Main och epic ska ha rätt registrerade worktrees och
vara fria från smuts, dolda indexflaggor och pågående merge/rebase. En ändrad Git-bas
under bedömningen ger `TASK_SELECTION_GIT_CHANGED`.

## MCP och svar

Operatörens Python-komposition aktiverar verktyget genom
`RuntimeService(..., task_selection=selection_service)` och `create_server`.
CLI har ännu ingen automatisk spec-/boardregistrering; ingen implicit tjänst aktiveras.

```json
{"project_id":"registrerat-lokalt-project", "epic_run_id":"registrerad-produkt-run"}
```

MCP `task_get_next` är read-only. Bara den autentiserade Integration-anslutningens
epic får väljas. Worker, Coordinator och främmande scope nekas före HTTP-läsning.
Roll, status, specifikation, proof, kandidat och slot kan inte skickas som argument.

Svaret innehåller `next_task_id` (nullable), prioriterade `candidates`, `tasks` med
`runnable`, versionssiffra och `blockers` (`code`, `related_id`), beroendenas bevis-ID:n,
externa förutsättningar samt observerade epic-/main-SHA. `reservation=false` gäller
alltid. Tom kandidatlista är ett lyckat lässvar med konkreta orsaker, inte task-Failed.

| Blockerare | Åtgärd |
| --- | --- |
| `TASK_NOT_PLANNED`, `TASK_ALREADY_OWNED` | Återuppta befintlig run eller avsluta dess process; skapa inte en andra ägare. |
| `TASK_GIT_RESOURCES_OCCUPIED` | Stäm av upptagen branch/path; adoptera eller radera inte okända resurser. |
| `TASK_SPEC_MISSING/INVALID/BOARD_MISMATCH` | Registrera/rätta fullständig operatörsspec och boardkontrakt. |
| `DEPENDENCY_MISSING/CYCLE/INVALID/NOT_DONE` | Rätta grafen eller invänta angivet beroende. |
| `DEPENDENCY_RUNTIME_NOT_DONE` | Slutför beroendets review/integration/verifiering. |
| `DEPENDENCY_DELIVERY_UNVERIFIED` | Stäm av konkret approval, kontext, merge, eftertest, Done-händelse och stopp. |
| `EPIC_PREREQUISITES_UNSPECIFIED`, `EPIC_DEPENDENCY_*` | Registrera beroende/scope/egen acceptans eller invänta faktisk epic-Done. |
| `EPIC_MAIN_DELIVERY_UNVERIFIED` | Verifiera tidigare epic på main och synka aktuell epicbas innan nytt urval. |
| `TASK_PREREQUISITE_UNVERIFIED` | Uppfyll de listade externa förutsättningarna och läs om. |
| `EPIC_NOT_ACTIVE`, `EPIC_OR_MAIN_WORKTREE_UNSAFE` | Stäm av aktiv epic och säkra arbetsläget. |

## Gräns mot start och recovery

Urval ändrar inga runs, slots, övergångar, journaler, boardstatusar eller Git-commits.
Integrationslåsets fil kan skapas i Git common-dir; ingen Worker startas eller stoppas.
Upprepning och återöppnad SQLite ger samma svar så länge fakta och konfiguration är
oförändrade. Ett nytt svar är råd utifrån observerade fakta och ingen reservation.

Start måste återvalidera board, scope, beroendebevis och kapacitet före beständig claim
eller resume. F15 startar inom samma epic; urvalsresultatet kringgår inga av dess
grindar. [F28](F-28-slots.md) utökar reservationen till konfigurerade 1–2 Workers;
F29 kopplar nytt boardurval till scheduling och start. Native parallellitet provas
i F30. Manuell utvecklingsbootstrap adopteras
inte som produkt-runtime. F38:s dokumenterade miljö-/autonomigate kvarstår.

## Verifiering

`tests/test_task_selection.py` använder verkliga temporära Git-repositories,
worktrees, SQLite, F15/F16/F18/F20/F21-taskleverans och F08-epicintegration.
Boardtransport, Codex/Herdr och processobserver är simulerade. MCP-provet använder
verklig SDK-klient och lokal server. Proven verifierar urval utan sidoeffekter,
prioritet/ordning, upprepning/återöppning, negativa grafer/specs/roller, READY/APPROVED,
fullständig delivery och tidigare epicens mainmerge/slutverifiering. Inget native
parallelitetsprov eller säker autonom drift påstås i F27.
