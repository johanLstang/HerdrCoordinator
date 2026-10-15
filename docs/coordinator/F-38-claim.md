# F38: projektägare och nästa epic

## Tjänster och operatörskontrakt

`CoordinatorRuntimeService` startar en faktisk långlivad Codex-session i projektets
rena main-arbetskatalog. Operatören tillhandahåller `Settings`, skyddad F04-principal
med projektroll Coordinator, stabilt run-ID, Herdr-server och `CoordinatorSpec`.
Ingen EpicRun eller Workerplats används för projektägaren. Alla native-ID:n kommer
från verkliga adaptersvar och kontrolleras mot process, pane, cwd och Codex-thread.
Versionerad [policy](../../prompts/coordinator-v1.md) paketeras i wheel/sdist.
Native ACK binds till projekt/run/operation/configuration/initial main-SHA och en
verifierad originalprompt; tidsbevis från Codex-journalen krävs inom 45 sekunder.

`CoordinatorClaimService` kopplas till denna runtime och `TeamPlayerReader`, samt
en betrodd fabrik för F34 `EpicStartService`. Konfigurationen innehåller explicita
native/local-ID:n för alla epics/tasks, komplett epic-/taskspec, prioritet och
backloggordning. Boardfixtures utanför listan kan aldrig bli kandidater. Fabriken
måste använda samma Settings/StateStore, reserverad run, rätt Integration-principal,
exakt spec och faktisk TeamPlayer-synk. F34 behåller sina ursprungliga registrerings-
och återförsökskontroller.

`CoordinatorControlService` tillsammans med
`RuntimeService(..., coordinator_control=...)` exponerar enbart:

| Verktyg | Argument | Effekt |
| --- | --- | --- |
| `coordinator_overview` | `project_id` | Läser kandidater, blockerare, gate och aktiv reservation. |
| `epic_claim_next` | `project_id`, `request_key` | Väljer/reserverar/startar en verifierad epic eller svarar WAIT. |

Actor, specs, kommandon, gatebevis och vald epic kan inte skickas i verktygsargument.
Varje anrop validerar den betrott registrerade anslutningen mot verklig aktuell
session/ACK/process och konfiguration. Övriga verktyg nekas utan fallthrough, även
om andra tjänster injiceras av misstag. Integration och Worker kan inte claima.
Stop är en operatörsfunktion; agenten får inget generellt muterande runtimeverktyg.

## Val och reservation

Färska get_me/list_projects/list_epics/list_tasks krävs. Claim kräver Write och
exakt autentiserad User-tilldelning för varje ingående task. Binding, medlemskap,
namn, acceptans, beroenden och taskprioritet jämförs med planens verifierade spec.
P0–P3 och därefter explicit backloggordning bestämmer valet. Interna taskberoenden
kan vara planerade vid epicstart; Integration gatear deras faktiska taskstarter.
Epicspecens externa förutsättningar kräver operatörens `prerequisite_probe`.

Ett föregående epic-Done på boarden räcker inte. Registrerade scopes/taskspecs,
aktuella review-/merge-/eftertest-/inaktivitetsbevis och faktiskt godkänd
Epic→main-leverans verifieras genom `TeamPlayerEvidence`. Leveransens commit måste
finnas i aktuell main. Saknat underlag lämnar en namngiven blockerare.

Projektets privata flock och repositoryts gemensamma `herdr-integration.lock`
hålls under claim. SQLite BEGIN IMMEDIATE och `project_claim_owner` hindrar en
andra reservation även mellan processer/anslutningar. Reservationen sparar owner,
nyckel, unik epicrun, main-SHA, konfigurations-/spechash, runtimegate och skäl när
en tidigare kandidat hoppats över. Därefter görs verklig F05-preparation och
basen kontrolleras före F34-start. Ingen utvecklingsbootstrap adopteras.

Samma nyckel återfinner ursprunglig operation. En ny nyckel under aktiv reservation
återger EXISTING och startar inget. Partiell start behåller reservation och F34:s
ursprungliga journal; okända effekter kan aldrig ersättas med en ny epicrun.
F38 frigör inte reservationen. F40 ska göra detta först efter faktisk slutleverans.
En främmande redan aktiv boardepic, en Pending-epic med påbörjade tasks eller
en oavslutad registrerad EpicRun utan samma reservation stoppar nya claims och
visas som hinder utan kandidater i overview. Även retry på eget claim nekas
när en andra främmande epic finns.
WAIT skapar inga epic-/taskruns, worktrees, Integration, Workers eller falskt Done.

## Runtimegrind och sessionskonfiguration

`runtime_gate` är operatörens verifierare, inte ett agentargument. Utan verifierare
är start spärrad. `RuntimeGateEvidence` kräver aktuell konfigurationshash/main-SHA,
identifierad provkälla samt verifierad nekad outside-read, skyddad state,
Git-rollgräns, isolerad verifieringsprocess och input-readiness. Alla boolar är
strikta. Förändrad miljö/bas/config kräver ny verklig verifiering.

F17:s faktiska brister är fortfarande olösta. En operatorprobe som bara skriver
`True`, en godkänd repo eller ett verktygsgodkännande etablerar ingen OS-gräns.
Det förberedda nativeprovet använder därför ingen positiv gate och kan bara
återge verifierad WAIT. Det intygar ingen full autonom målmiljö/F41-framgång.

`CodexMCPLaunch.disabled_servers/disabled_plugins` ger sessionsbundna CLI-val,
utan ändrad global config, credentialöverföring eller godkännandepolicy.
Default-argv/fingerprint för äldre starter bevaras. Officiell
[MCP-konfiguration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) beskriver
`enabled=false` och pluginers separata enabled-inställning. Verklig native
verktygsinventering ska även kontrolleras; detta är inte shell/OS-isolering.

## Schema 3 och återhämtning

Ta en säkrad backup när ingen skrivoperation pågår innan äldre state öppnas med
ny version. Schema 1/2 migreras atomiskt genom enbart nya projektobjekt; gamla
payloadbytes, relationskolumner, index och leveranshashar skrivs inte om.

| Ny persistens | Ägarskap och relation |
| --- | --- |
| `coordinator_runs` | En oavslutad projektägare; immutable actor/run/config/initial-SHA/cwd. |
| `project_operations` | FK till faktisk Coordinator och samma projekt; unik project/kind/key. |
| `project_claim_owner` | Högst en reservation per projekt tills explicit verifierad RELEASED. |

Epics/Tasks/Review/Operation/ExternalReference/TransitionEvent har oförändrade
format och foreign keys. ProjectOperation behöver ingen artificiell EpicRun.
STOPPED kräver fysisk inaktivitet, processgruppbevis och bekräftad shell i rätt
pane. Native-ID:n/historik bevaras; stoppade projektägare adopteras inte.

Vid WORKSPACE_PENDING utan känt svar: stoppa nya effekter och avstäm resurserna
mot den sparade operationen/etiketten. Vid AGENT_PENDING: läs exakt registrerat
agentnamn/process/cwd; en saknad/ändrad agent ger hinder. Vid DISPATCH_PENDING:
läs originalthread/korrelerad ACK; skicka aldrig prompten igen. Vid EXIT_PENDING:
avstäm fysisk inaktivitet före STOPPED och frigör inte ägarskap på timeout.
Databasfel, ändrad nativeidentitet, gate, owner, main eller spec ger verifierings-
hinder; ändra inte Done eller skapa ersättningsruns för att dölja avvikelsen.

## Aktuell verifiering

Serviceproven använder faktisk temporär Git/SQLite/F34 och SDK-MCP, med uttryckligt
kontrollerade Herdr/Codex/TeamPlayer/process- och positiva gateadaptrar.
De verifierar positiva starter, prioritet/ordning, race, återförsök, scope, owner,
schema 1/2-migration och negativa gränsfall. Detta är ingen native säkerhetsattest.
Native Coordinator-start/ACK/omstart/WAIT/stopp är verifierade; [faktiska bevis](F-38-native.md). Acceptans slutmarkeras efter aktuell utvecklingsreview, Task→Epic och integrationskontroller.

Aktuell korrigerings- och fullverifiering finns i [utvecklingsreview](../reviews/F-38-review-1.md). Nativeprovets eget godkännande och slutgrindar kvarstår.
