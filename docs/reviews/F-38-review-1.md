# F38: första utvecklingsgranskningen

**Beslut: CHANGES_REQUESTED; ingen merge eller task-Done.**

Granskad checkpoint `4e8fb47480a42a1c6f4d716e46c2dc86fa3b5aae`, aktuell
main/epicbas `b6ceb9956f512a2ed2330fe24b2cd741d83738fb`. GitAdapter säkrade
hela diffen: 23 filer, 143206 bytes, SHA256
`4b828b7ba6e6f9ad8325e764d8eb01de5e3d4ed3ca2165dbd164cfe09107e090`.
Den manuella bootstrapen adopteras inte till produktens run-/ägarlivscykel.
[Maskinläsbart beslut](F-38-review-1.json).

## Fynd kopplade till F38.A2

1. En whitelistanpassad boardepic i InProgress utan Coordinator-reservation hindrar
   inte att en annan Pending-epic väljs och får en andra Integration.
2. En redan registrerad aktiv EpicRun i samma projekt utan claimreservation hindrar
   inte nästa start, även om boardstatus ännu är Pending.
3. En Pending-epic med redan påbörjad task lämnar också nästa epic valbar;
   InProgress/Testing/Paused/NeedsReview/Done utan ägarskap måste stoppa ny start.

Två isolerade regressionstester med faktisk temporär Git/SQLite och kontrollerad
board/runtime reproducerade båda felen: 2 failures, exit1, 4.83 sekunder. Ingen
verklig TeamPlayer- eller nativeproduktresurs ändrades av dessa tester.

Korrigeringen ska kontrollera både boardens och persistensens främmande aktiva
epics under projektets/repositoryts lås, före reservation eller F34-effekt.
WAIT med konkret blockerare och tom kandidatlista i overview krävs; eget befintligt
claim ska fortfarande vara idempotent. Regressioner ska ingå i taskens tester.

Korrigering förbereds i en separat temporär reviewkopia medan checkpointens
sammanhängande 954-testkörning avslutas. Projektets kod/policy/testfiler är hittills
oförändrade mot dess 123 sparade källbindningar. Den körningen ensam godkänner
inte fynden. Slutreview kräver korrigerad commit och faktiska nya provresultat.

Native F38-r1:s nya approvaldialog väntar fortfarande på mänskligt beslut;
start/ACK/samma-SID-omstart är observerade, claim-WAIT och fysisk stopp återstår.
F38 förblir NeedsApproval/Attention och E10 Active, respektive acceptans0/3.

Ytterligare fem isolerade fall reproducerade fynd3 på checkpointen:
5 failures/exit1/7.58s. Korrigeringen omfattar även started task på Pending-epic,
och stämmer av både overview och claim innan nya resurser reserveras.

## Korrigering implementerad och verifierad i task-worktreet

Alla tre fynd är åtgärdade genom den gemensamma board/runtime-kontrollen under
projekt-/repositorylås före reservation och F34. Egna befintliga claims undantas
från den främmande ägarkontrollen; en andra främmande epic stoppar även ett eget
retry. Overview återger inga kandidater vid sådant hinder.

Full checkpointkörning: **954 PASS/2320.94s/exit0** på 4e8fb474; alla123
kod-/test-/policy-/konfigbindningar oförändrade genom körningens slut.
[Fullt JUnit](F-38-source-4e.xml), [källbindningar](F-38-source-4e.json).
Därefter ändrades endast claimservice och dess tester. På faktisk korrigerad
Task-kod: **41 PASS/21.65s/exit0**, inklusive31 befintliga och10 nya regressioner.
[JUnit efter korrigering](F-38-correction.xml), [exakta nya källbindningar](F-38-correction.json).
De övriga121 källfilerna matchar fullkörningen byteidentiskt. Ruff/build/READMECLI
passerar, wheel matchar exakt67 moduler ochfyra policies.
Detta redovisar två faktiska körningar; en ny full964-suite har inte körts.

Korrigeringskopians första prov saknade paketerade policyfiler och gav33
setupfel före produktoperationer. Efter att de fyra oförändrade resurserna lagts
in passerade33 bas-/ursprungsregressioner, sedan41 med utökade taskstatusfall.
Den faktiska taskkörningen ovan bekräftar resultatet i rätt worktree.

**Slutreview/READY_FOR_REVIEW/Task→E10/eftertester/Done återstår.** Den verkliga
F38-r1-approvaldialogen är olöst; native checkpoint4e har endast startup/ACK/
samma-SID-reopen/overview bekräftade. Claim-WAIT/slutrapport/fysisk stopp ska säkras.
Det äldre utvecklingsreviewbeslutet bevaras; detta är en korrigeringscheckpoint,
inte ett slutgodkännande eller native positiv OS-/autonomibevisning.
