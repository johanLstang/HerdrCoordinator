# F39 Worker-leverans

**Status:** READY_FOR_REVIEW. Task `0657116a-4cf9-483f-aa9f-57612d7cee39`, branch
`task/e10-f39`, bas/avsedd E10-target `ae122afe0d0754d30080ddd4cbe3cd5ba110819b`.
Exakt överlämnad taskcommit registreras efter denna dokuments commit i
READY-recept och den följande slutreviewen; en själv-refererande commit-SHA skrivs
inte in i sitt eget träd. Worker har inte integrerat Task→Epic eller satt Done.

## Levererat

- Registreringsbunden slutreview med faktisk F36-återvalidering, fullständig
  huvud-/epicdiff, aktuella taskbevis, källor/krav/acceptans och oberoende tester.
- Beständiga kontexter/läsrecept/beslut. Godkännande kräver komplett aktuellt
  underlag och faktiskt exit0; verkligt testfel stöder endast underkännande.
- Purpose-bound faktisk taskcreate, säkrade intentioner, äkta native-ID:n,
  additiv verifierad plan och återkoppling till samma Integration-SID. Avbrott
  återförsöker saknat journalsteg, inte skapande eller levererad prompt.
- Ursprungliga F34-registreringar och tidigare Done-/review-/mergebevis bevaras.
  Schedulerns ägare behålls endast med verifierad projektion och i övrigt samma
  konfiguration. Föråldrade vyer nekas.
- R1: projekt-Coordinator utan egen epic-review_context kan använda en registrerad
  epics exakta egna granskningskontext. Andra inställningsskillnader eller felaktig
  identitet/acceptans nekas före effekt.

## Faktisk verifiering och källgräns

| Verifiering | Resultat och källa |
| --- | --- |
| Full README-suite | 973 PASS,4322.63s,exit0; [baselinesource](F-39-source-eded.json), [JUnit](F-39-source-eded.xml) |
| Nytt gruppdispatch-/avbrottsprov | 1 PASS,91.24s; [JUnit](F-39-dispatch-crash.xml) |
| Slutlig R1/F39-suite | 12 PASS,176.68s,exit0; [nuvarande source/kommandon](F-39-r1.json), [JUnit](F-39-r1.xml) |
| Native före/efter | Faktisk exit1 → korrigering → exit0/current approval; [scenario](../coordinator/F-39-native.md), [bindningar](../coordinator/F-39-native.json) |
| Lint/build/READMECLI | PASS på aktuell kod |
| Aktuellt wheel | Exakt73 källmoduler/fempolicies; isolerad wheelimport/policy PASS |

Baseline och native körde produktkällan på checkpoint eded024a. Alla81 bindningar
var oförändrade fram till faktiskt stopp. Därefter ändrades endast
`coordinator_review_subject.py` för R1; övriga80 bindningar är oförändrade. Alla12
aktuella F39-prov kör projekt-Coordinator utan egen granskningskontext och omfattar
full korrigeringskedja, felaktig child-konfiguration, create-/review-/dispatchavbrott,
MCP-/roll-/scopegränser, läsrecept och ändrad main. Stängd faktisk nativekonfiguration
är kompatibilitetskontrollerad utan ny runtime eller stateeffekt. Ingen fullständig
973-omkörning påstås efter denna enda avgränsade rättning och inget nytt liveprov
påstås genom servicetesterna.

## Begränsningar och fortsatt integration

Nativeprovet krävde verklig input-/panerecovery genom existerande tjänster; detta
är öppet redovisat. D02/F17:s OS-/autonomigrind är fortfarande stängd. Inga utvecklings-
worktrees adopterades, inga F38-claims fabricerades och native main är oförändrad.
Alla egna runtimeprocesser/server är stoppade, med bevarad SQLite/historik/refs.

F40 behöver konsumera effektivt medlemskap/approval och hantera epics separata
granskningskontext i start-/fortsättningskompositionen. F39 exponerar ingen main-
merge eller fortsättningsloop. Integration ska granska exakt överlämnad taskcommit,
aktuell E10/main och samtliga artefakter, integrera genom GitAdapter/common-lock och
bootstrapundantaget samt verifiera den faktiska mergens tree/parents och eftertester.
