# F44: återställning av ursprungliga operationer

## Kontrakt före kod

Operatören komponerar `OperationRecoveryService` med F43:s ursprungliga skyddade kontext och konkreta registrerade Integration-/Coordinatorleveranstjänster. Inga startfabriker eller agentstyrda roll-/tillståndsargument. En explicit originaloperationsidentitet väljer högst en avgränsad återställning; journalen hämtas alltid på nytt från samma projekt/SQLite. Rapporter är ingen effektbehörighet.

Start återanvänder ursprunglig F12/F18/F34/F38-operation, konfiguration, principal och sessions-/slotbevis. Vid okänt workspace eller obekräftad session eskaleras Attention utan ny start eller resursadoption. Befintliga skyddade intents och observerade ACK återanvänds, aldrig dubbla prompts.

Merge fortsätter från ursprunglig Task/F08/Coordinatorleverans och operation-ID. Faktiska två parents och operationstaggen måste återfinnas; avvikande Git stoppar utan ny merge. Bara saknat test-/stopp-/Done-/synksteg fortsätter. Okänt testresultat använder en beständig ny verifieringsnyckel knuten till originaltestet och exakt leveranscommit; den gamla mätningen ersätts inte med ett påhittat resultat. Befintligt verifierat resultat återanvänds.

TeamPlayer återläser identitet, tilldelning, aktuell version/status och exakt historikmarkör. Ett okänt kommentar-/historikutfall avstäms genom originalsynken; senare manuella ändringar ger Attention. Inga gamla kommentarer skickas blint på nytt. Oavslutade rena testjournaler kan markeras som ersatta först efter ett faktiskt nytt committbundet resultat.

Beständig recoveryjournal redovisar ursprunglig operation, ägare, aktuell observation, konkret nästa åtgärd och säkra felkoder. Ett misslyckande frigör aldrig slot och fabricerar inte Done. Alla effekter behåller ursprungstjänstens lås/roller/Gitkontroller. F43-övervakningsposter på faktiskt stoppade resurser kan avstämmas utan resume när ursprunglig konfiguration, fysisk inaktivitet, session, Git och färsk board bekräftas; affärsfas och historik bevaras. Saknat bevis lämnas Attention.

## Verifieringsplan

Felinjicering vid start-, merge-, test-, stopp- och synkgränser med faktisk temporär Git/SQLite och tydligt kontrollerade runtime-/boardpeers. Kontrollera samma SID/runs/worktrees, högst två slots, ingen dubblerad start/merge/prompt/kommentar samt ändrad roll/konfiguration/HEAD/worktree/board och unknown-result. Native- och maskinomstartsanspråk kräver separata faktiska prov; F45 ansvarar för samlade avbrottsprov.

## Komposition och avgränsad användning

```python
operator = OperationRecoveryService(
    original_session_recovery,
    controls=[original_integration_control],
    delivery=original_coordinator_delivery,
)
receipt = await operator.recover(original_operation_id)
```

Alla objekt är konkreta ursprungstjänster för samma SQLite/projekt. Återstart återskapar dem från skyddad originalkonfiguration; MCP får inget nytt verktyg för roller, adoption eller recovery. Pending epicstart och epicboardsynk kräver dessutom den riktiga ursprungliga Coordinatorns verifierade anslutning; en serialiserad roll i en journal räcker inte. Metoden tar inte emot gamla rapporter som effektbehörighet och anropar ingen claim-/startfabrik.

Stödda originaljournaler: coordinator_start/stop, epic_start, task_start samt underordnad start_runtime/dispatch_assignment; task_merge med merge/testbarn; coordinator_epic_delivery med mainmerge/testbarn; teamplayer_sync; samt F43:s tekniska monitorposter när ursprunglig fysisk stoppning bekräftas. Ohanterade, FAILED och CONFLICT-operationer ger Attention med original-ID och konkret operatörsåtgärd. Ingen generell automatisk konfliktlösning eller cleanup införs.

RECORDED_COMPLETE betyder bara att originaljournalen redan är avslutad; det är ingen ny live-/Done-/mergeverifiering. RECOVERED betyder att originaleffekten avstämts/slutförts. WAITING och ATTENTION är beständiga tekniska hinder som F42 fortfarande blockerar ny scheduling för. Originaloperationen, saknade steg och originaltjänsterna måste fortfarande läsas. Ett separat kvarvarande synkintent väljs med sitt eget sparade ID.

Ett funnet ACK kan ha blivit beständigt medan parent och board ännu visar startläge. F44 verifierar då färsk identity/owner/spec/dependency och den ursprungliga claim-/assignmentjournalen, slutför dess checkpoint och synkar efteråt. Den vanliga schedulerstarten får inte förväxla detta bevisade mellanläge med ett nytt Worker-uppdrag.

Okända tester återkörs under originaltjänstens Gitlås och exakt commit-/kommandokontroll, med en ny stabil nyckel härledd från senaste okända testintentet. Även förlust efter ett återförsök ger nästa explicita recovery en ny journal, aldrig ny merge. Äldre PENDING-testjournaler blir SUPERSEDED först när faktisk exit0, samma merge/source/target/command och leverans-/manifestkoppling verifierats. Historiska resultat ändras inte till PASS. Kända misslyckade tester kräver operatörsbeslut. Coordinatorleveransen återfinner ett redan godkänt sluttest via F08:s aktuella commit-/manifestkontroll om parentcheckpointen saknas.

Stoppad monitoravstämning är läsande mot native/Git/board och skriver bara tekniska journaler. Den kräver originalregistreringens hash/principal, hash på föregående monitorpost, faktisk stop-/processgruppsbevisning, samma session, färsk Git/board och upprepad inaktivitetskontroll efter avstämningen. Affärsfas, completed_at, session, worktree och slot ändras inte och inget resume skickas.

## Faktisk avstämning av F43:s avslutade prov

Båda ursprungliga tekniska monitorjournaler är avstämda mot riktig Herdr/Codex/Git/TeamPlayer. Alla agenter förblev stoppade och slots0; runs/referenser/events/reviews och ursprungskonfigurationen är oförändrade. Bara tekniska journaler ändrades, utan native-/boardskrivningar eller återöppning. [Faktiskt kvitto och skyddat arkiv](F-44-native-settlement.json). Separat F43-arkiv bevaras byteidentiskt. Detta intygar inget nytt avbrotts-, merge- eller maskinomstartsprov.

## Slutverifierad kandidat

Funktionell kod `c2be7d9436f34b6918cceeafbfd6c661c092928d`:30 recoveryprov PASS352.36s och24 samlade leverans-/tvåepicsfall PASS1343.36s,54 unika fall. Kontrollerade native-/boardpeers med faktisk temporär Git/SQLite. Full slutgrupp inkluderar kanoniska nycklar, exakt aktuellt boardintent och korrupt avslutad ägare. Den avbrutna fixturkörningen räknas inte. Riktig separat F43-monitoravstämning har uttrycklig källkvalificering. [Verifieringsunderlag](F-44-verification.json). Levererad: sourcea4c09df APPROVED, faktisk Task→E11no-ff18415ef och12 eftertester PASS150.22s/exit0; TeamPlayer Done8/User105 återläst. [Slutreview](../reviews/F-44.md).
