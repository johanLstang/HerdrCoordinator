# F38-r1: förberett verkligt Coordinatorprov

Detta är förberedelseunderlag, ingen intygad nativeframgång. Utvecklings-F38 väntar nu i
NeedsApproval/Attention, E10 Active och acceptans 0/3. F37-r1:s godkännande gäller den avslutade
F37-sessionen; det ger inget förhandsgodkännande för en ny F38-dialog.

## Avgränsning och identiteter

| Resurs | Faktiskt förberedd bindning |
| --- | --- |
| Lokal projektplan | `f38-probe-r1` / `f38-epic-r1` / `f38-task-r1` |
| Reserverat Coordinatorrun-ID | `9997e70a-5bec-4521-8145-4f6499507d80` |
| Namngiven egen server | `hc-f38-r1-20261011`, före start not running |
| Native boardepic | `29066c6e-c787-49f5-adcf-6e2207c42a12`, Pending1 |
| Native testtask | `030ffc3b-f2f2-4f5a-ac15-eb9da3e56b86`, Pending1/User105/Critical |
| Skyddad operatörskonfiguration | `/tmp/herdr-f38-r1-operator-20261011/config.json`, 0600/0700 |
| Ny egen state/provrot | `.herdr/probes/f38-r1` i task/e10-f38, 0700 |
| Faktisk redan godkänd reporot | `.worktrees/task-e06-f26/.herdr/probes/f26/repo` |
| Oförändrad ren fixture-main före start | `d4215a26ba5e77d64a1fd4aed4117e5313c43563` |

Det reserverade UUID:t är ännu ingen registrerad session. Inga native workspace-,
pane-, agent-, session- eller process-ID:n fabriceras. De ska komma från
`CoordinatorRuntimeService` och faktiska Herdr/Codex-svar.
Originala F37-sessioner, SQLite, refs och worktrees bevaras.

## Förberedda verktyg och scenario

[Skyddad värd](../../tests/native_f38_host.py) använder verklig TeamPlayer-anslutning,
färsk User105/Write, explicit task/epicbinding, ny schema3-state och projektets
Coordinator-tjänster. Operatörens F04-profil ligger utanför repositories.
`runtime_gate=None` är den verkliga olösta D02/F17-grinden, ingen positiv testattest.
Startfabriken nekas dessutom i denna provvärd om den skulle nås av misstag.

[Operatörsverktyget](../../tests/native_f38_probe.py) startar ursprunglig
Coordinator-intention, observerar thread/state, skickar en enda journalförd
kontrollprompt och stoppar enbart egen runtime. Operatören gör inget epicclaim,
Integration-/Workerstart, taskarbete eller merge åt agenten.

Planerat faktiskt scenario:

1. Ursprunglig native Coordinator-ACK i main utan tools; beständiga faktiska ID:n.
2. Omöppnad SQLite/komposition återfinner samma run/SID utan extra prompt/agent.
3. Samma Coordinator anropar `coordinator_overview` och `epic_claim_next` med stabil
   nyckel. En planerad kandidat finns, men faktisk guard ger WAIT före reservation.
4. Thread/MCP-resultat/SQLite/Herdr/TeamPlayer/main verifieras: inga epic-/taskruns,
   Workers, claims, branches eller mainändringar och native boarddata stannar Pending.
5. Fysisk STOPPED/processgrupp/shell verifieras före projektägarens avslut. Bara den
   egna namngivna servern stoppas; historik och resurser bevaras.

Sessionens CLI-inventering är faktiskt verifierad med installerad Codex0.162.1:
`herdr_coordinator` enabled; `node_repl`/`teamplayer` disabled; ärvda plugins avstängda.
Globala configfiler och approvalpolicy ändras inte. Första launcherprovet upptäckte
att CLI-keypaths inte tolkar TOML-citerade nycklar; detta rättades före native start
med validerade enkla identifierare. Ingen session startade vid det konfigurationsfelet.

En eventuell verklig ny approvaldialog ska inspekteras mot denna specifika SID,
projekt och verktyg. Herdr-skillen kräver mänskligt beslut innan dialogen besvaras.
Det blir ingen global Always allow eller användning av F37/r5:s äldre godkännande.

## Bevis som fortfarande saknas

Verklig start/ACK/omstart/native MCP-WAIT/fysisk stopp och rapport återstår.
Positiva claim/start/race är separata serviceprov med faktiskt Git/SQLite/F34/SDK
och tydligt kontrollerade runtime-/board-/gateadaptrar. De verifierar funktion och
failure recovery; de är ingen OS-säkerhetsattest eller full autonom F41-leverans.
Task-Done kräver även aktuell utvecklingsreview, faktisk Task→E10 och eftertester.

## Faktisk start och nytt approvalhinder

Checkpoint `4e8fb47480a42a1c6f4d716e46c2dc86fa3b5aae`, egen server PID222855/start114325421, Herdr0.9.3/protokoll22. Faktisk projekt-Coordinator registrerad med original-ACK inom den sparade 45sekundersgränsen: run `9997e70a-5bec-4521-8145-4f6499507d80`, agent `hc-c-e0ae9e37ee994158a7ee8f45`, pane w1:p1, SID `01a12809-f3e2-7753-bb10-9ae22f326102`. Omöppnad SQLite/komposition återfann samma run/SID/ACK utan extra agent eller assignment.

En enda journalförd kontrollprompt skickades. Samma agent anropade faktiskt `coordinator_overview`; service gav RUNTIME_SECURITY_GATE_UNVERIFIED, inga aktiva epics eller reservation. Nästa `epic_claim_next` ligger i verklig ny approvaldialog för projekt f38-probe-r1/nyckel f38-r1-native-claim-v1. Dialogtext och scope är sparade, inget val skickat. Herdr-skillens krav på mänskligt beslut gäller den nya dialogen; F37/r5:s tidigare tillstånd utökas inte. Användaren tillfrågad om endast detta anrop.

State/Git visar 0 epic-/taskruns, 0 claims/Workers/merger och ren oförändrad main d4215a26. Native guard-WAIT, slutrapport och fysisk stopp återstår. TeamPlayer NeedsApproval9/lokal Attention, kriterier0/3; E10 kvar Active8/egenacceptans0/3. Full954-suite på checkpoint/123 källbindningar kör separat; 107 foundation-, 137 boundary- och 64 integrationsprov har passerat. Ingen native full OS/autonomigaranti ges och inget task-/epic-Done intygas i detta hinderunderlag.

## Verifierad utvecklingskorrigering medan native-dialogen väntar

954 sourceprov passerade på checkpoint4e (2320.94s/exit0/123 oförändrade bindningar). Därefter rättades tre reproducerade ensam-epic-brister i claim/overview; 41 claimprov passerar på faktisk korrigerad Task-kod (21.65s/exit0), övriga121 källfiler byteidentiska. [Review, historik och nya SHA-bindningar](../reviews/F-38-review-1.md). Nativeprovet är uttryckligen bundet till sin ursprungliga checkpoint4e/session/configuration; inget nytt native claimresultat eller stopp intygas genom de ändrade servicetesterna. Dialogen väntar fortfarande på eget mänskligt beslut.
