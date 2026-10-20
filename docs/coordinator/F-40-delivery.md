# F40: verifierad epicleverans och fortsättning

**Arbetsstatus:** implementation/verifiering i `task/e10-f40`. Ingen verifierad
utvecklingsleverans eller genomfört nativeprov intygas av detta kontrakt.

## Registrerad komposition

Registrera hela den ändliga uppsättningen `CoordinatorReviewPolicy` innan rooten
startas. `review_capability(policies)` och `delivery_capability(review_hash)` binds
till `CoordinatorRuntimeService.capabilities`. Rootens inställningar kan sakna
epicspecifik review_context. Varje faktisk F34-start får sin exakta egna
`EpicReviewSpec`; andra inställningar får inte skilja sig från rooten.

Bygg `CoordinatorReviewSubject` endast för faktiska registrerade produktkörningar.
`CoordinatorReviewService(runtime, subjects, policies=policies)` tillåter ännu
ostartade epics i policyregistret. Subject-vyerna måste exakt matcha registrerade
policies och root. Att lägga till nästa faktiska run ändrar inte capabilityhashen.
Utan explicit policyregister behålls den tidigare F39-kompositionen.

Koppla `CoordinatorDeliveryService(review, claims)` till
`CoordinatorControlService(claims, review=review, delivery=delivery)`. Sätt den
betrodda `claims.completed_plan` till `delivery.completed_plan`. Claims läsare
behöver explicita bindningar även för verifierade korrigerings-ID:n;
`delivery_contexts` och `scopes` måste ange varje levererad epics verkliga egna
inställningar och effektiva tasklista. Original projekt-/F34-spec muteras inte.

Endast denna komposition exponerar `coordinator_epic_deliver`:

```json
{
  "project_id": "registered-local-project",
  "epic_run_id": "actual-registered-epic-run",
  "request_key": "stable-delivery-request",
  "verification_key": "stable-final-verification-attempt"
}
```

MCP tar inga specs, SHA, roller, kommandon, gate-/tests-passed-booleaner eller
operatörsauktorisationer. Integration/Worker får ingen ny förmåga.

## Beständig ordning

| Steg | Verifierad effekt och återförsök |
| --- | --- |
| INTENT | Aktuell komplett F39-approval, exakt board/tasks/owner/medlemskap, root/F34-bindning och authorization; avsikt med fasta F08/F13-nycklar sparas före merge. |
| MERGED | F08 utför/reconcilerar faktisk no-ff och exakt parents/manifest/approval. En känd merge upprepas aldrig, även efter förlorat svar eller en ny anropsnyckel. |
| STOPPED | F13 stoppar samma Integration-run/session och bekräftar fysisk inaktivitet. Ingen session/worktree/ref tas bort eller ersätts. |
| VERIFIED | Betrodd F08-verifiering körs på exakt main-merge med approvalens kommandokonfiguration. Samma nyckel återanvänder känt resultat. |
| LOCAL_DONE | Aktuella faktiska bevis ger intern Done-händelse. Board kan fortfarande vara Active; reservationen hålls. |
| RELEASED | F23–25:s CAS-/återläsningsjournal bekräftar Epic Done; fysisk stopp och Git/testbevis kontrolleras igen före atomisk release av denna roots verkliga claim. |
| Fortsättning | En separat beständig nyckel går till F38:s färska val/claim. Nästa run använder då aktuell main och dess beroendebevis; gate/ordning/ensam epic gäller fortfarande. |

Det finns högst en leveransintention per epic-run. Alla faktiska merge-, stopp-,
test-, status- och fortsättningsoperationer behåller sina riktiga ID:n. Tidigare
reviews/taskleveranser och kända delresultat skrivs inte över.

## Hinder och recovery

- Ändrad main/scope/approval före merge kräver ny aktuell review. Ingen merge eller
  automatisk synk/reset används för att kringgå detta.
- Ett fel efter merge visar faktisk merge/SHA och kvarvarande fas. Epicen blir
  inte Done och nästa epic startas inte. Bevara det faktiska Git-resultatet.
- Sluttestfel behåller MERGING/board Active. Samma verifieringsnyckel återger felet;
  välj en uttrycklig ny verifieringsnyckel för en ny faktisk kontroll efter åtgärd.
  Okänt processutfall kräver avstämning; gamla nyckeln startar ingen ny process.
- Stopp som inte kan bekräftas håller leveransen och reservationen kvar. Återförsök
  samma F13-avsikt; skapa ingen ny Integration-session.
- Okänt Done-svar återläses genom befintlig CAS-journal. När endast board-synk
  återstår upprepas inga Git-, test- eller stoppeffekter.
- Efter verifierad release får en befintlig fortsättningsclaim återhämtas med sin
  egen nyckel; inga ersättningsruns eller dubbla epics skapas.

## Säkerhetsgrind och begränsat produktprov

Generell autonom leverans kräver faktisk betrodd positiv D02/F17-runtimegate för
den aktuella rootkonfigurationen/main. Frånvarande gate ger WAIT utan effekt.

Det separata operatörsanropet `authorize_bounded_product_probe` är inte ett
agentverktyg. Det kräver en faktisk egen F39-attachment till manuellt registrerad
produktkörning, ingen aktiv F38-claim, och ett exakt aktuellt granskningsbeslut.
Auktorisationen binds till root-SID/run, epic, review/kontext, source/main,
medlemskap, konfiguration/kommando och repo. Operatörens konkreta beslut sparas
beständigt. Det är en begränsad manuell provauktorisation, ingen positiv OS-grind
och ingen behörighet att claima nästa epic. Utvecklingsworktrees adopteras aldrig.

`native_f39_host.py` behåller sin äldre standardkomposition och kan explicit
komponera F40 med `enable_delivery` och ett fullständigt `review_policies`-register.
`native_f40_probe.py authorize-delivery` kräver en skyddad exakt användargodkänd
manifestfil. Native agenter utför fortfarande task-/review-/deliveryanropen själva.
Tidigare F39-sessionstillstånd eller verktygsgodkännande gäller inte nya sessioner.

Serviceprov använder riktig temporär Git/SQLite/processer/MCP och kontrollerade
native/board/security-adaptrar. Faktiska liveprov och deras manuella ingrepp ska
redovisas separat. Full positiv autonomi hör till F41 och dess externa grind.
