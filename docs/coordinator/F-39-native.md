# F39-r1: verklig epicreview och länkad korrigering

**Faktiskt resultat 2026-10-15:** Samma registrerade Coordinator underkände först
leveransen, skapade en verklig korrigeringstask och godkände därefter uppdaterad kod
och tasklista. Samma Integration utförde båda leveranserna genom Workers och services.
Samtliga egna runtimeprocesser och den namngivna servern är nu stoppade.
[Maskinläsbara faktiska bevis](F-39-native.json).

## Avgränsning och identiteter

| Resurs | Faktiskt registrerat värde |
| --- | --- |
| Källa som körde nativeprovet | `eded024a88b4401bc6695b2a5aa36fe2f9ca9505`; 81 produkt-/konfigurationsbindningar oförändrade fram till stopp |
| Namngiven egen server | `hc-f39-r1-20261015`, PID3503543/start154134547; stoppad, PID frånvarande |
| Lokal projekt-/epicbindning | `f39-probe-r1` / `f39-epic-r1` |
| Projekt-Coordinatorrun | `b1b7cfe9-f71f-4ae5-ab0d-63b1f516ba84` |
| Faktisk Coordinator-SID | `01a13fc4-45b2-71d1-9e3c-fa720a87a911` |
| Faktisk Integrationrun | `0e219621-d55c-4efc-b52f-f9cdec1fed10` |
| Faktisk Integration-SID | `01a13fc5-4e15-78f3-afc4-6d7bab6b7723` |
| Native boardepic | `cc1051d1-0666-4fb7-b0a7-aa458d02f0e1` i HerdrCoordinator, verifierad User105/Write |
| Ursprunglig native task | `d9ee85a3-f657-4155-8b3c-6ce82d8d6f2f`, User105 |
| Verkligt skapad korrigeringstask | `40d9b2f3-7a4e-45a3-a002-8c9db65d5106`, User105; lokal `f39-output-r1` |
| Egen skyddad state/provrot | `.herdr/probes/f39-r1` i task/e10-f39, 0700 |
| Skyddad operatörskonfiguration | `/tmp/herdr-f39-r1-operator-20261015/config.json`, 0600/0700; SHA256 `665c63698e38587e9e7e5b10d3221e8c51101b3f42792dd06c72e59cc2c50eca` |

Fixturen ligger i den redan godkända ofarliga F26-reporoten. Ny schema3-state och
nya riktiga F34-/Coordinatorregistreringar används; inga utvecklingsworktrees
adopterades och ingen F38-claim fabricerades. Original-ACK/process/SID/configuration
återvaliderades. Root/Integration exponerade sina scoped MCP-metoder; verklig
stdio-kontroll verifierade att Worker-profilen har noll kontrollverktyg. Ärvt
TeamPlayer/node_repl och plugins var avstängda i respektive launchprofil.

Användaren godkände uttryckligen **”Ja, godkänn F39-r1-verktygen”**. Varje verklig
dialog kontrollerades mot rätt faktisk SID och registrerat scope. Intention/UI-hash
sparades före input; engångs-/sessionsval användes. Dolda argument i dialogens
begränsade preview ersattes inte av gissade värden: tjänstens autentiserade
projekt-/epic-/taskkontroller behölls. Inga globala Always allow-val gjordes.

## Verkligt före/efter-förlopp

| Bevis | Före korrigering | Efter korrigering |
| --- | --- | --- |
| F36-handoff | `3cc9a75b-29ea-4f15-92a6-3a5f55b877bb` | `30421f78-19d6-4ab5-9db8-c06fa777fa40` |
| Epic-SHA | `3d2f59a1d7c3ab3da6654305cc5e42511df401e1` | `33f2d7685b40d3b619a040495396d5328ae24758` |
| Main-SHA | `d4215a26ba5e77d64a1fd4aed4117e5313c43563` | Samma oförändrade main |
| Faktisk komplett diff | 238 bytes, bara inputfil | 451 bytes, bevarad input och ny outputfil |
| Aktuellt taskmedlemskap | `f39-seed-r1` | Originaltask samt `f39-output-r1` |
| Oberoende verifiering | `e77c5f86-5726-4e18-859e-6fd90fcb45e1`, TEST_FAILED/exit1 | `69bdb360-f0ac-4d7d-95fa-d53c8e3be63b`, SUCCEEDED/exit0 |
| Coordinatorbeslut | CHANGES_REQUESTED `5b4172c0-2031-47b8-8bc6-660f7cc5156a` | EPIC_APPROVED/current=true `90b5cbe8-4a58-42f6-85db-f63e2f25c05a` |

Originaltasken levererade exakt sju bytes `Hej Ada` i `f39_input.txt` på commit
`76a3723422a4a2ee421923e642e27a571b079cb7`. Dess avgränsade kriterium var uppfyllt;
baselinens aggregate-kontroller verifierade input och redovisade explicit att
epicens slutoutput saknades. Handoff var inte ett epicgodkännande.

Coordinator läste alla tre chunks i kontext `a558654546adc8a6288a65dc2db984720d8a38d7b48cdc6cc9fdeebbe6f33910`.
Två samtidiga läsanrop gav COORDINATOR_BUSY och återförsöktes korrekt. Faktiska
läsrecept finns för samtliga index. Fyndet hänvisade till exakt registrerat kriterium,
hela diffen och faktiskt oberoende exit1. Tjänsten skapade korrigeringen med riktiga
native-ID:n, spårbar review-/entryhash och samma Integration-SID.

Korrigerings-Workern levererade endast `f39_output.txt`, exakt åtta bytes
`Hej Ada` följt av en newline, på commit `264dd9db300341a27891e2f4581ce49b7063c987`.
Input och övriga filer bevarades. Samma Integration granskade aktuell komplett diff,
integrerade Task→Epic och körde eftertester/fysiskt stopp innan Done och ny F36.

Coordinator läste alla tre nya chunks i kontext
`53532cb9866bc249492cf9ecde8025dc39b2e25d3deed76b0b612614a5ebef7f`.
Oberoende verifiering av faktisk uppdaterad kod gick från exit1 till exit0. Nytt
F08-review-ID är `c45ea74d-a803-4b2a-a845-59c873cc0e4f`; tidigare beslut och
taskleveranser finns kvar. Tolv faktiska Coordinator-MCP-anrop är identifierade i
beviset. Inget operatörsanrop fattade nativeagentens reviewbeslut åt den.

## Verkliga hinder och återupptagning

Den återanvända fixturens äldre AGENTS kräver explicit samma-session-input för
greetingprefix. Båda Workers rapporterade detta, parkerades genom F31 och
återupptogs genom F32 med det redan i respektive task/README fastlagda `Hej`.
Samma faktiska SID behölls:

- Seed: `01a13fcc-d2d6-76c1-a546-e3e01a49a2c6`.
- Korrigering: `01a13fef-1f06-7031-8b71-2223530a0707`.

Korrigeringens första workspacekontroll skedde innan den nya shellpanelen var
redo och gav RUNTIME_PANE_NOT_IDLE_OR_OWNED. Starten behöll run
`0354f52c-c80f-4d2e-b1a0-ea9cc68ea71d`, slot och faktisk w4:p1/terminal
term_65de1a36980cb4. Operatören verifierade senare rätt cwd/Gitbas, endast faktisk
idle bash och frånvarande agent. Native Integration återförsökte samma registrerade
task_start med exakt sparad spec; tjänsten återvaliderade och återanvände intent och
panel. Ingen ersättningsrun eller fabricerad readiness användes. Första felet är
bevarat separat från den senare lyckade starten.

## Stopp och kvarvarande leveransgräns

Aktuell approval, manifest, exakta filbytes, oförändrad main och faktisk
Worker-inaktivitet verifierades före stopp med rätt Worker-adapter. Integration
stoppades först. Första Coordinatorstoppet gav wrapperfelet
TEAMPLAYER_CONNECTION_FAILED efter verklig exit; återläsning visade EXIT_PENDING
och frånvarande agent. Samma stoppnyckel slutförde enbart fysisk kontroll, utan
en andra interrupt/exit. Alla egna runtimeprocesser är STOPPED, noll agenter/slots.
Egen server stoppades efter kontroll av faktisk PID/starttid/argv; not running och
frånvarande PID3503543 är verifierade. Historik, SQLite, refs och worktrees bevaras.

Logisk provepic är APPROVED, boarden Active, eftersom F39 inte utför Epic→main.
Ingen main-merge, F38-claim eller positiv D02/F17-/F41-autonomiattest görs. Operatörens
verkliga input-/readinessåtgärder redovisas öppet; detta var ett manuellt produktprov.

Efter fullständigt fysiskt stopp rättas ett separat granskningsfynd om olika
epicers granskningskontext. Körd nativekälla och 973-baseline binds fortsatt till
checkpoint eded024a; den senare konfigurationsrättningen verifieras separat.
Utvecklingens READY_FOR_REVIEW, slutreview, Task→E10, efterprov och Done-synk redovisas
i leveransunderlaget, inte som redan uppfyllda genom detta nativeprov.
