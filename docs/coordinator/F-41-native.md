# F41: verifierat komplett nativeflöde

2026-10-20: **F41.A1–A3 är verifierade.** Två riktiga provepics har levererats genom Herdr/Codex, Git, SQLite och TeamPlayer. Utvecklingstaskens egen källreview, Task → E10 och eftertest återstår innan task Done; E10 behåller Active inför sin PR/mainintegration.

## Faktisk leverans

| Steg | Faktiskt resultat |
| --- | --- |
| Ursprunglig prov-main | `2631592730a89380712222e2538bc9cbb1780094` |
| Första epicens slutgranskade källa | `04ab78f3b59322e29241ae318b76cc10e3c2e4fd` |
| Första mainleveransen | `1ddaa78c7b867de89d7c322debfbf2385abf5571` |
| Nästa epic/taskbas | Exakt `1ddaa78c7b867de89d7c322debfbf2385abf5571` |
| Andra epicens slutgranskade källa | `0c1faac1d52714f2008462c58fe31abc4b0aa028` |
| Slutlig prov-main | `e685602d8ea11610035ed25dee853a5c035b9d15` |
| Färsk board | Två epics Done; A/B/C/PUBLIC-r1/D Done och User105/blitterbot@gmail.com |
| Runtime | Coordinator, båda Integrations och alla fem Workers fysiskt STOPPED; slots null |

Båda mainmergernas faktiska två föräldrar matchar den granskade target/source. Varje task har en faktisk granskad Task → Epic-merge, oberoende eftertest med exit0, rätt session och stoppbevis. Nästa epic genomfördes hela vägen till en andra leverans.

## Acceptans och roller

- **A1:** Integration valde/startade A/B/C, granskade och integrerade dem; Coordinator slutgranskade och levererade. Operatören valde inga tasks och gjorde inga produktmerges.
- **A2:** A:s riktiga NFC-brist gav korrigering i samma Worker-SID och nytt commit/godkännande. Första epicreviewns oberoende API-test misslyckades med exit1; en riktig User-tilldelad PUBLIC-r1 skapades och skickades till samma Integration. Dess nya underlag fick nytt aktuellt godkännande efter exit0.
- **A3:** Ny epic startade från första mainmergen, verifierade tidigare Done-tasks med deras registrerade egna kontrakt och levererade D. 673 tidslinjesampel har högst två unika Worker-slots och högst en oavslutad epic; 58 sampel har två reserverade Workers.

Coordinator använde exakt sina sex registrerade MCP-verktyg. Integrations gjorde kritiska operationer genom sina rolltjänster; nästa Integration läste också egna AGENTS.md/README.md i läsprofilen. Workers använde endast sina två egna MCP-verktyg för workspace/commit samt egen sandbox för implementation/test. Faktiska native MCP-kataloger/anrop och Git-/processbevis kontrollerades separat från modellrapporter.

R18:s två reserverade Workers omfattar arbete och väntan på review; det påstås inte att två modellturns samtidigt beräknades i denna körning. Ett tidigare separat R16-prov har verkligt samtidiga working-processer. R18:s slot-, roll-, Git- och Donebevis står för sig.

## Aktuella kvitton

Alla sex första reviewdelar, åtta korrigerade delar och tre delar för nästa epic har verkliga läskvitton och kontrollerade innehållshash. Oberoende verifiering gav i ordning exit1, exit0 och exit0. Korrigerade och nästa underlag avser olika aktuella commits och egna godkännanden.

[Slutaudit och verkliga kvitton](F-41-native-final.json) innehåller Git-föräldrar, run-/session-/operations-ID:n, profiler/sourcepins, tester, fysisk stopp, faktiska verktygsanrop och färsk TeamPlayer-version/owner/status. Den läsande slutauditen gav **noll databasändringar**. [Arkivmanifest](F-41-native-archive.json) anger beständig privat kopia med SHA256; originalrefs, worktrees och state har inte flyttats/adopterats eller städats.

## Miljö och avstämning

Fryst produkt/provkälla: `eae85f1779a602ddbfaa74a4a314d9f9bacee12d`, 91 bytekontrollerade filer. Faktisk installerad Codex0.162.1 på Linux; erbjuden uppdatering skippades. Separat native preflight gjorde egen brokercommit och stoppades före positiv runtimegrind. Grinden mätte faktisk profil-/testisolering, verifierade nativebevis och bands om till ny verklig main före fortsättning. Inga globala Codexinställningar ändrades och äldre osäkrad standardprofil ger inte automatiskt denna rättighet.

R18:s första Coordinator-start gav inget verifierbart ACK, noll claims och noll epic/task-runs. Samma osäkra send skickades inte om. Originalrun/config/deadline sparades och fysisk STOPPED verifierades före en ny separat operatörsregistrering i samma godkända fixture. Hela ovanstående autonoma kedja kördes av denna nya Coordinator, med en enda journalförd initial huvudbegäran. Integration-aktivering, scheduling, review, korrigeringar och merges gjordes därefter av registrerade produkttjänster.

Observatören återstartades efter ett transient SQLite-läsfel. Produktjournaler och native trådar är auktoritativa; en oavbruten extern pollinglogg påstås inte. Alla avslut kontrollerades genom faktiska process-/pane-/stoppbevis. Tidigare försök och upptäckta fel är bevarade i [historiken](F-41-history.md) och [progressunderlaget](F-41-native-progress.json).
