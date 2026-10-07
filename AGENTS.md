# Instruktioner för HerdrCoordinator

## Arbetsmodell och källor

Utveckla HerdrCoordinator en feature/task i taget. TeamPlayer styr aktuell status och tilldelning; [Backlog.md](Backlog.md) styr scope, ordning, beroenden och acceptanskriterier. Följ [Utvecklingsprocess.md](Utvecklingsprocess.md) för hela arbetsgången.

Läs dessutom [Epic och Feature Guide](Epic%26Feature%20Guide.md), [Herdr_Workflow.md](Herdr_Workflow.md) och relevanta avsnitt i [arkitekturplanen](Arkitektur%20och%20implementationsplan%20%E2%80%93%20Herdr%20-Codex%20Multi-Agent%20Workflow.md). Den manuella utvecklingsprocessen här bygger produkten som senare ska kunna köra två Workers. Produktens samtidighet är ett krav att implementera och verifiera; starta inte parallella implementationstasks för att efterlikna den.

Läs närmaste underordnade AGENTS.md innan filer där ändras: [src/AGENTS.md](src/AGENTS.md), [tests/AGENTS.md](tests/AGENTS.md) och [prompts/AGENTS.md](prompts/AGENTS.md). Användarens aktuella instruktioner styr uppdraget; dokumentera nödvändiga förändringar av projektregler så att underlagen stämmer överens.

## TeamPlayer och identitet

- Projekt: **HerdrCoordinator**, projekt-ID `d2ee4c75-7b80-465f-83ac-1750854a8e80`.
- Utförare: **blitterbot@gmail.com**, verifierat användar-ID `105f26a7-0648-438d-94fd-3260ac3af4ee`.
- Lokala epic-ID:n är `E-01`–`E-12`; feature/task-ID:n är `F-01`–`F-50`. Använd UUID från Backlog.md i MCP-anrop.
- Första kandidaten är **F-01**, task-ID `9bc95f85-f05d-4842-b517-c1f8132c49ab`, under E-01.

Vid start av implementationsarbete: kör `list_projects` och `get_me`, verifiera projektets namn, ID och Write-åtkomst samt autentiserat konto. Läs sedan `list_epics` och `list_tasks` för detta projekt. Kontrollera `executionOwnerKind=User` och `responsibleUserId=get_me.userId` för vald task; ett äldre `responsibleAgentId` är inte bevis för aktuell utförartilldelning.

Board epics saknar utförar- och leveransstatusfält i nuvarande MCP-kontrakt. Följ epicens leveransstatus i backloggen och senare EpicRun. Ändra inte andra projekt eller skapa ersättningstasks för att dölja ett felaktigt ID.

## Välj och plocka en task

1. Stäm av TeamPlayer mot backloggens Kanban. Återuppta först redan pågående eget arbete om det finns. Välj annars nästa Planned-task i backloggens ordning och implementationsfas 1–12, inom användaruppdragets omfattning.
2. Läs taskbeskrivning, arbetsinstruktion, acceptans, källor och externa villkor. Beroenden inom epicen ska vara granskade och mergade till epic-branchen. Föregående epic ska vara verifierad och mergad till main.
3. Dokumentera blockerare och skäl till ändrad ordning i backloggen och TeamPlayers tasktext innan en senare oberoende task väljs. Åsidosätt inte beroenden med en statusändring.
4. Förbered rätt branch/worktree. Hämta sedan `get_task` med aktuell version och kontrollera tilldelning och beroenden igen.
5. Före kodändring: uppdatera `Pending` till `InProgress` via `update_task_status` med aktuell version och konkret statusReason. Kontrollera svaret och sätt feature- och Kanbanstatus till `Active` i backloggen. Vid versionskonflikt: läs om tasken och bedöm förändringen innan nytt försök.

En begäran om dokumentation, planering eller instruktioner börjar inte automatiskt F-01 eller någon annan implementationsfeature. Ändra taskstatus först när motsvarande implementationsarbete verkligen startar.

## Branches worktrees och roller

- Epicen har `feature/epic-<epic-id>` från aktuell main, exempelvis `feature/epic-e01`, i eget worktree. Dess samlade leverans integreras där.
- Varje feature har `task/<epic-id>-<task-id>` från aktuell epic-branch, exempelvis `task/e01-f01`, i separat worktree. Implementera i detta task-worktree.
- Worker implementerar, testar och committar egen task. Worker mergear inte, byter inte branch, skriver inte andra worktrees och uppdaterar inte TeamPlayer direkt.
- Integration-rollen granskar, synkroniserar task mot aktuell epic, integrerar Task → Epic och ansvarar för taskstatus.
- Coordinator-rollen slutgranskar epic, öppnar PR och integrerar Epic → main.

Under bootstrap kan den ansvariga utvecklaren utföra rollerna sekventiellt. Ange rollövergång vid review och merge; Worker-uppdragets gränser består under implementation. När respektive orchestratorservice finns utförs kritiska operationer genom den. En framtida service behandlas inte som redan tillgänglig.

Bevara befintliga lokala ändringar. Kontrollera branch, worktree, arbetsläge och bascommit före arbete. Kodberoenden till annan epic hämtas normalt efter dess main-merge. Synkronisering epic → task är en särskild integrationsoperation; leveransmerge går Task → Epic → main med `--no-ff`.

## Implementation och verifiering

Leverera taskens hela avgränsade resultat med relevanta fel-, omstarts- och återförsöksfall. Bygg endast den struktur som behövs. Vid scopeändring: uppdatera först backlogg och styrande kontrakt, därefter TeamPlayers beskrivning och acceptans med färsk version. Fortsätt när båda beskriver samma uppdrag.

Kör meningsfulla kontroller för de berörda kraven. Prioritera tillstånd, persistens, idempotens, rollkontroll, worktree-isolering, samtidighet och aktuella reviewcommits. Använd temporära Git-repositories, SQLite-filer och avgränsade testepics. Redovisa verkliga integrationer separat från simulerade adaptrar. För dokumentändringar räcker relevanta länk-, innehålls- och konsistenskontroller.

Projektet har initialt dokumentation och instruktionsfiler. Fastställ start-, test- och buildkommandon i F-01 och dokumentera dem; kör inga påhittade projektkommandon. Markera bara acceptans som faktiskt verifierats.

## Status och hinder

Använd de etablerade lokala Kanban-värdena `Planned`, `Active`, `Attention`, `Done`. De motsvarar planerad, pågår, behöver åtgärd och klar.

| TeamPlayer API-status | Lokal Kanban | Användning |
| --- | --- | --- |
| Pending | Planned | Ej startad task. |
| InProgress, Testing, Paused | Active | Arbete, verifiering eller tillfällig paus; Paused är inte en blockerarrapport. |
| Blocked, NeedsInput, NeedsApproval, NeedsBudgetApproval, Failed | Attention | Dokumenterat hinder, beslut, budgetbehov eller misslyckad körning. |
| NeedsReview | Attention | Väntan på faktiskt granskningsbeslut utifrån. |
| Done | Done | Granskad, integrerad och verifierad task. |

Vanlig review/fix-loop ligger kvar i `InProgress` eller `Testing`. `Cancelled` kan synas i TeamPlayers Done-kolumn men uppfyller inte taskens acceptans eller definition av Done; redovisa avgränsning uttryckligen.

Vid hinder: ange orsak, vad som provats, behövd input/åtgärd, berörda resurser och nästa ansvariga roll. Skriv detta i backloggen och TeamPlayers taskbeskrivning/statusReason med aktuell version. Vid avbrutet nätanrop: återläs för att avgöra om skrivningen genomfördes innan samma ändring upprepas. Ett hinder kring TeamPlayer får inte leda till en falsk lokal framgångsstatus.

## Task Done och epicintegration

Worker lämnar `READY_FOR_REVIEW` med task-ID, branch, commit, tester och begränsningar. Integration-rollen kontrollerar senaste epicbas, genomför review, integrerar godkänd task och kör relevanta integrationstester. Först därefter får tasken sättas till `Done` med färsk TeamPlayer-version.

Uppdatera taskens kriterier, featurestatus, Kanbanrad och verifieringsunderlag i Backlog.md. Registrera testkommando/resultat, granskad task/epic-SHA och merge-SHA. Epicens antal verifierade kriterier räknas från dess egen acceptans, inte antalet avslutade tasks. Stäm av status innan nästa task väljs.

När alla tasks är Done: kör samlad build/test/epicacceptans, öppna PR mot main, granska diff, kontrakt, schema/migrationer, rollkontroll, recovery och dokumentation. Verifiera mot aktuell main och gör om berörda kontroller vid ändrad bas eller konflikter. Coordinator integrerar efter godkänd slutreview. Epicen blir Done efter main-merge och slutverifiering, med PR-referens och mergecommit i backloggen.

Om Git-merge lyckas men verifiering eller TeamPlayer-synk misslyckas: behåll faktisk merge-SHA, dokumentera kvarvarande steg och sätt inte Done i förtid. Återförsök endast steget som saknas.

## Granskningsregler

- Flagga Done utan review, faktisk merge och verifiering.
- Flagga start som kringgår tilldelning, beroende, unik taskägare eller slotreservation.
- Flagga godkännande som avser äldre task-, epic- eller maincommit.
- Flagga kritiska operationer som litar på agentens rollargument eller rapport i stället för verifierade fakta.
- Flagga hemligheter i kod/loggar/prompts samt cleanup av osäkrat eller aktivt arbete.

Rapportera ändrade filer, verifiering, TeamPlayer- och backloggstatus samt konkret kvarvarande åtgärd. Dessa instruktioner ger ingen automatisk delegering till parallella implementationsagenter.
