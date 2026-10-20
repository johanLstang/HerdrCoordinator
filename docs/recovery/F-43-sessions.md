# F43: återanslutning till registrerade sessioner

## Kontrakt före implementation

Källor: arkitektur §§7,23,28,45; workflow §§17–18,38; Backlog F43.

Operatören återskapar endast tjänsteobjekten med de ursprungliga skyddade inställningarna, anslutningarna och F34/F38-registreringarna. Ingen resurs adopteras, ingen startfabrik anropas, ingen ny run eller ersättningsworktree skapas. Worker-behörighet, slot och input kommer från originaljournalerna, aldrig från en rollsträng eller återhämtningsrapport.

SessionRecoveryService återkopplar verifierade Coordinator-, Integration- och Worker-sessioner och registrerar deras övervakning beständigt. En avgränsad poll läser färsk runtime- och sessionstatus för samma identiteter. Sparad övervakning är en historisk observation, inte auktoritet för nästa sidoeffekt. Efter processomstart byggs samma tjänst från konfiguration och samma SQLite.

- Live: verifiera originalregistrering, ägd Git-resurs, HEAD, fysisk runtime, Codex-session och board/tilldelning. Återställ övervakning utan start eller prompt.
- Bekräftat stoppad Integration: samma Codex-session kan återupptas med F13, samma registrering och beständig originalnyckel. MERGING/avslutad epic lämnas till sin ursprungliga leveransoperation.
- PARKED utan verifierat sparat svar: bevara parkering, fråga, session och ledig slot. Ett svar skapas aldrig av recovery.
- PARKED med sparat F32-beslut: validera beslut, källa, generation och HEAD; använd TaskResumeService med samma input/resume-nycklar och kapacitetskontroll. Ett okänt promptutfall observeras; input skickas inte igen.
- Saknad eller ändrad referens: beständig teknisk Attention med resursidentiteter, säker orsakskod, vad som observerats, operatör som ansvarig och konkret nästa åtgärd. Ingen slotrelease, radering eller ersättningsagent. En otillgänglig transport bevisar inte förlorad sessionsdata.
- TeamPlayer: teknisk Attention kan speglas som NeedsInput genom en verifierad återhämtningsjournal. Affärsfasen och reservationen bevaras. Återställd verifiering häver överlagringen och ger en ny synkrevision. Epics förblir Active. Misslyckad synk redovisas som väntande.

Övervaknings-/Attentionjournalen binds till ursprunglig registrering, skyddad principal och exakt observerad run. Avstämningen blockerar ny scheduling medan ett återhämtningshinder består. Efter ändrade resurser krävs färsk verifiering; äldre journaler och rapporter ger ingen behörighet. Databasschema3 behålls med befintliga operationsjournaler.

Generell in-flight-recovery av start, merge och synk ingår i F44. En avslutad CoordinatorRun är immutable och återöppnas inte. Saknad fysisk process utan bekräftat inaktivitetsunderlag leder till Attention även om Codex-metadata finns kvar.

## Verifiering

Planerad: verklig temporär Git/SQLite, restart med samma identiteter, fel roll/config/resurs, oförändrad smutsig Worker, parkerad task med/utan sparat svar, två upptagna slots, okänt inpututfall utan omsändning och kontrollerat saknad sessionsreferens. Separat verkligt Herdr/Codex-prov använder nytt ofarligt repository och avgränsad TeamPlayer-testepic. Ingen tidigare F41-session återstartas.

Inga acceptanskriterier är ännu markerade som uppfyllda.

## Operatörskomposition

```python
from orchestrator.application.session_recovery_service import SessionRecoveryService

recovery = SessionRecoveryService(
    settings, store, reader,
    project_id=original_coordinator.spec.project_id,
    coordinator=original_coordinator,
    integrations=[original_epic_start],  # även RegisteredIntegrationPlan stöds
    resumers={original_epic_start.principal.epic_run_id: original_task_resume},
)
result = await recovery.poll()  # en avgränsad, färskt verifierad omgång
```

Objekten byggs från samma skyddade operatörskonfiguration och samma SQLite-fil.
De får inte komma från MCP-argument. Det finns inget nytt agentverktyg för att
påstå roll, förlust, inaktivitet eller tillstånd. Om Coordinatorns vanliga
claimservice ska fortsätta efter återstart kopplar operatören även dess befintliga
`register_epic_observer(original_epic_start)` till originalkontexten; recovery
anropar aldrig dess startfabrik.

Resultatet per run är CONNECTED, WAITING_INPUT, WAITING_CAPACITY, INPUT_PENDING,
ORIGINAL_OPERATION_REQUIRED eller ATTENTION. CONNECTED registrerar aktuell
native status och en cursor över riktiga turn-ID:n/statusar, utan meddelanden,
svar eller prompts. Nästa `poll()` återläser runtime och native tråd; journalen
medger ingen effekt på egen hand. Vanliga kontrolltjänster driver scheduling och
review efter återhämtningen. En aktiv teknisk hold stoppar också befintlig
scheduler-advance och direkta taskskrivningar genom Integrationens kontrollgräns;
verifierad fysisk parkering är fortfarande tillåten.

`session_recovery` använder befintlig operationsjournal för Epic/Task och
ProjectOperation för Coordinator. Nyckeln är originalrunens ID. Originalets
registrerings-ID/hash och skyddade principal binds till exakt observerad run;
observationer har hash, revision och bevarad övergångshistorik. Ingen ny tabell,
nullable-ändring eller migration behövs. En fil-låsning serialiserar recovery-
omgångar; F13/F32 behåller sina egna effekt-/tasklås och atomära slotregler.

Färsk Git-/board-/SQLite-avvikelse stoppar återupptagning. Endast ett skyddat
redan beständigt resume-intent får avstämma en förväntad processförändring efter
okänt resume-resultat. Samma nyckel används; ingen ny start skickas.

TeamPlayer NeedsInput hävs automatiskt bara när den tidigare recovery-skrivningen
är bekräftad, dess faktiska historikblock finns kvar och extern version fortfarande
är just den bekräftade versionen. Ett senare manuellt beslut eller okänt synkutfall
lämnas som väntande originaloperation. Epicen är fortsatt Active. En hold som
återstår på en avslutad run syns fortfarande i F42-inventeringen och lämnas till
F44:s explicit avstämda originaloperation; F43 återöppnar inte avslutade runs.

## Pågående verifiering och verkligt prov

Kontrollerade peers: 18 riktade prov PASS75.38s före den sista kontrollgränsgrinden,
samt tre riktade hold-/återställningsprov PASS18.53s efter grinden. Slutlig samlad
körning och regressionsunderlag fylls i när körningarna avslutats. Faktiska Git-
repositories och SQLite används; dessa prov är inga native- eller maskinomstartsbevis.

Det verkliga provet har egen server `hc-f43-r1-20261020`, skyddad katalog
`/tmp/herdr-f43-r1-20261020`, frysta källfiler och separat TeamPlayer-testepic
`1d66d61c-452e-4769-b578-63de5168f4b0`. Provets tre tasks är User105:
A `953176e9-ee80-4175-955e-7d567a1de083`,
B `cf002888-8749-4e22-8a9f-7f7740554d9d`,
C `ebc797c2-ebd0-4257-8052-08fa2e793bdb`.
De är fasta JSON/input-prober med read-only sandbox, tom egen MCP-katalog,
andra servrar/plugins avstängda, inga verktygsautogodkännanden och ingen
kodimplementation, merge eller extern kommunikation. Detta prov gör inget nytt
anspråk på F17:s isolerings- eller autonomigrind.

Före någon native resurs skapats rättades ett saknat `version=1` i provdriverns
CoordinatorEpic-konfiguration. Det ursprungliga felet maskerades av adapterns
generiska anslutningsfel; SQLite visade noll runs/operationer vid rättningen.
Ingen produktkälla eller registrerad resurs skrevs om. Ursprunglig provkonfiguration
är bevarad som `config-before-driverfix.json`.

Den faktiska CoordinatorRun som därefter skapats är
`1114f161-54c9-453d-8efa-0f02460e7314`, STARTING, originaloperation AGENT_PENDING.
Agent `hc-c-f43f4267ddf84867aeaa920f` i `w1:p1` väntar i Codex UI vid
”Update available 0.162.1 → 0.163.0”. Ingen prompt eller native ACK har skickats,
Codex-session-ID är ännu null och det finns noll EpicRun/TaskRun/Worker-slots.
Herdr-skillen kräver användarbeslut innan en blockerad dialog besvaras. Frågan
om att välja **Skip** är ställd; inget uppdateringsval har utförts. Återuppta
samma startoperation efter beslutet och verifierad UI-status; skapa ingen ny run.
Native återanslutningsacceptans är ännu inte uppfylld. Frysta provkällor bevaras;
den efterföljande kontrollgränsgrinden i scheduler/Integration verifieras separat
i de kontrollerade MCP-/pipelineproven.


### Slutlig källsnapshot inför fortsatt nativeprov

Originalsnapshoten är bevarad. Före någon native prompt/ACK eller återhämtnings-
omgång skapades en separat `frozen-recovery-source` med de slutliga källorna,
inklusive hold-grinden och verifiering av stoppad korrigeringsplan. 91 pins binds
explicit till dessa filer. Den ursprungliga MCP-hostpathen behålls i processen.
Den omkomponerade Coordinatorns konfigurationshash är exakt oförändrad:
`4278b586d5bdfc332673723ceca5b4552273684c3723d245e2a74bed977f3756`.
Kontrollen gjorde noll SQLite-skrivningar; samma run och operation
`cf06e48d-ecba-4304-af87-a1744ba265ae` är kvar. Inga nya resurser adopteras
eller påhittas. Underlaget finns privat i `snapshot2-proof.json` och båda äldre
konfigurationssnapshotarna är bevarade. Fortsatta prov använder den nya frysta
källan och samma originalregistrering; detta är ännu inget native acceptansbevis.

## Avstämt kodunderlag

Funktionell commit `5bd6f539bb8747bc53b6fa0f968dd5483113ddf8`. Slutliga20 PASS104.75s,119 regressionsprov PASS584.17s och7 kompletterande kontroll-/projektprov PASS357.73s;143 unika fall, källskillnader redovisas uttryckligen. [Verifieringsunderlag](F-43-verification.json) och [pågående review](../reviews/F-43-progress.md). TeamPlayer NeedsApproval5/User105 och E11Active11 med egenacceptans0/3 är återlästa. Nativeprov, full leveransreview, Task→Epic och eftertester återstår.
