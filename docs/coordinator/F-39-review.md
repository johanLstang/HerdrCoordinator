# F39: aktuell epicreview och spårbar korrigering

## Leveransläge

Implementation och verifiering är färdiga i `task/e10-f39`, från E10 `ae122afe`.
TeamPlayer F39 är ännu InProgress och E10 Active tills utvecklingsreview,
Task→Epic och eftertester passerar. Hela973-baseline och den efterföljande
12-provsverifieringen av en avgränsad konfigurationsrättning passerar.
[Verkligt före/efter-prov och stopp](F-39-native.md) är genomfört; serviceprov med
kontrollerade native-adaptrar redovisas separat. D02/F17:s autonomigrind består.

## Operatörskomposition

1. Registrera ett faktiskt `CoordinatorRuntimeService` med capability
   `coordinator_review=review_capability(policies)`. Hashen omfattar hela policyn
   och den versionshanterade `prompts/coordinator-review-v1.md`.
2. `CoordinatorReviewPolicy` anger originalets F34-specifikation och native-ID:n,
   autentiserad User, F36-kontrakt, oberoende verifieringskommando och tillåtna
   `CorrectiveTaskTemplate`. Agenten anger endast template-ID för numrerade fynd.
3. `CoordinatorReviewSubject` binds till samma state och projektinställningar samt
   faktisk F34-registrering. Projekt-Coordinatorn kan sakna egen review_context.
   En registrerad epics review_context måste exakt motsvara policyns ursprungliga
   EpicReviewSpec; övriga projektinställningar får inte avvika. Dess factory bygger F35/F36 från
   `RegisteredIntegrationPlan`, inte från gamla cachade taskspecifikationer.
4. Fabriken bygger selection med planens exakta native-ID:n/specifikationer/ordning,
   sync med aktuella lokala task-ID:n och scheduler med `registration=view`.
   `completion_for(policy, view.spec)` ger aktuellt F36-kontrakt. Tjänsten kontrollerar
   kompositionen innan den används.

F40:s fortsättningskomposition måste använda den effektiva planens task-ID:n,
medlemskap och nya granskningsbevis. Ursprunglig F38/F34-specifikation skrivs inte
om för att dölja korrigeringstasks. F38:s nuvarande krav på identiska startinställningar
behöver då hanteras uttryckligt för epicernas separata granskningskontext; F39 öppnar
ingen automatisk claim eller autonomigrind.
5. Tillför `review=service` till samma `CoordinatorControlService` som projektets
   claimtjänst. MCP tar projekt, epicrun, stabila nycklar, kontext-ID, läsindex och
   strukturerat beslut. Roll, commands, alternativa specs och testboolar nekas.

`correction_completion` är ett uttryckligt operatörskontrakt för ny tasklista.
Identitet, krav, källor och acceptansversion/text måste överensstämma med originalet.
Enbart literal `{task_count}` i betrott argv ersätts med det verifierade taskantalet;
det är ingen shellinterpolation eller agentstyrd kommandomall.

Schedulerns ursprungliga ägaridentitet bevaras endast genom verifierad additiv
planprojektion. Nya spec-/order-/bindingrader tas bort när originalets digest
rekonstrueras. All annan konfiguration ligger kvar i digesten och måste matcha
den befintliga ägarjournalen. Tidigare taskclaims eller schedulerägare skrivs inte
om. En gammal registreringsvy nekas efter medlemskapsändring.

## Aktuellt granskningsunderlag

Ett senaste faktiskt F36-handoff måste kunna återvalideras. Samtliga taskleveranser,
aktuella boardfält, taskreviews, mergeparents, eftertester och Worker-inaktivitet
kontrolleras igen. Oberoende operatörsverifiering körs genom F08 och binds till
aktuell epic/main samt taskmanifest.

Granskningsdokumentet innehåller krav, acceptansversion/text, källor, effektiv plan,
samtliga taskresultat, fullständig diff med ursprungliga base64-bytes/SHA256 och
läsbar patch, aggregate-resultat, oberoende verifiering, tillåtna templates och
begränsningar. Ogiltiga UTF-8-bytes visas som backslash-escapes i textversionen;
de fullständiga originalbyten är auktoritativa. För stora underlag nekas, inte klipps.

`coordinator_review_read` levererar högst 8192 tecken per chunk och sparar verkliga
läsrecept bundna till Coordinatorrun/kontext/index/hash. Beslut kräver hela aktuella
dokumentet. Påståendet ”jag har läst” ersätter inga recept. Ändrad main, epic,
tasklista/specifikation, acceptans, board, konfiguration eller policy kräver ny review.

APPROVED kräver exakt hela registrerade acceptansen och aktuell faktisk exit0.
CHANGES_REQUESTED kräver numrerade problem, konkret åtgärd och tillhörande kriterier.
Ett positivt faktiskt fel-exit från oberoende verifiering kan enbart stödja avslag.
Timeout, okänt processutfall eller stale verifiering ger inget kriteriebevis.

## Persistens och recovery

F39 använder schema3:s befintliga `project_operations`; ingen ny migration behövs.

| Journaltyp | Bindning och återförsök |
| --- | --- |
| `coordinator_review_context` | Unik projekt/requestnyckel, Coordinatorrun, handoff-ID/hash, source/main, medlemskap, board och konfiguration; fullständig oföränderlig dokumenttext/hash. |
| `coordinator_review_chunk` | Kontext/index/hash; alla segment måste finnas för samma Coordinatorrun. |
| `coordinator_epic_review` | Förslags-/kontexthash, beslut och templateval före F08-effekt; faktiskt F08-review-ID efteråt. |
| `coordinator_epic_correction` | Unik review/fyndnyckel, revision, template/spec-hash, create-intention, verklig native-UUID, planaktivering och samma-session-dispatch. |
| `coordinator_review_attachment` | Endast manuell operatörsväg för en faktisk registrerad produktprovrun; aldrig exponerad i MCP. |

F08 skriver review och epicstate atomiskt. Förloras därefter det yttre receptet
avstäms just denna review mot tidigare beslut/kontext/commits; beslutet återanvänds.
Det gamla underlaget görs inte om till ett nytt READY_FOR_REVIEW. Replay av ett
godkännande efter ändrad main ger HISTORICAL_APPROVAL, current=false.

Korrigering skapar nya tasks. Något verifierat separat reopen-API finns inte och
gamla Done-runs/commits/reviews skrivs inte över. Varje revision får unika lokala
och faktiska native-identiteter och en exakt reviewlänk. Före create sparas intent,
idempotensnyckel och beskrivningsmarkör. `TeamPlayerCorrectionCreator` kontrollerar
aktuell MCP-katalog, create-schema, Write och User. Den befintliga generella
skrivtransportens tre tillåtna verktyg utökas inte.

Vid okänt create-utfall läses board/task om och exakt markör/fält kontrolleras.
Ingen träff eller flera träffar ger Attention utan blind ny create. Alla tasks i
en beslutskorrigering måste faktiskt existera innan plan och epicstate aktiveras
atomiskt. Ägaren kan därefter starta rätt nya Worker genom den vanliga processen.

Återkopplingsintention och hash sparas före prompt till samma Integration-SID.
Okänt promptutfall avstäms mot exakt userMessage i originalets native thread;
en ny prompt skickas inte för att reparera ett saknat svar. Ett avbrott mellan
gruppmedlemmarnas kvittenser reparerar endast journalerna. Levererad återkoppling
är inte färdig korrigering: nytt faktiskt F36-handoff krävs före nästa review.

## Verkligt manuellt produktprov

[Nativevärden](../../tests/native_f39_host.py) rekonstruerar aktuell registrerad
plan för varje MCP-anrop. Coordinator och Integration har var sin skyddad profil
och launchkonfiguration. Worker får ingen board-/Coordinator-MCP-förmåga.
[Operatörsverktyget](../../tests/native_f39_probe.py) hanterar bara verklig
start/observation, journalförd kontrollprompt, explicit bindning och stopp.

Operatörsvägen kräver verklig F34-ACK/SID/process/configuration, samma skyddade
Coordinator-principal, verifierad worktreeägare/board och inga upptagna Workers.
Den skapar ingen F38-claim och adopterar inga utvecklingsworktrees. Produktens
automatiska claim behåller faktisk `runtime_gate=None` och kan inte starta.

Det verkliga före/efter-provet är genomfört med användarens separata F39-r1-beslut
och journalförda konkreta dialogval. F38:s engångsgodkännande återanvändes inte.
Nativekälla och senare R1-rättning har separata källbindningar. Epic→main och
full autonomi hör till F40–41.
