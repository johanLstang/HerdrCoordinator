# F40-r1: faktisk leverans verifierad och provet avslutat

Användaren godkände mapp/egna sessionsverktyg2026-10-16 och den separat
reviewbundna mainleveransen2026-10-20. Samma root/Integration/Worker utförde hela
första provepicens verkliga leverans. [Faktiska slutkvitton](F-40-native-final.json),
[ursprungligt fullständigt granskningsunderlag](F-40-r1-main-review.md),
[identiteter och historik](F-40-native.json).

| Kontroll | Faktiskt verifierat resultat |
| --- | --- |
| Worker | commit82b6b202, exakt Hej Ada/radbrytning; endast f40_greeting.txt tillkom |
| Task→Epic | granskad no-ff6033883834da04f296d29beeaa9fca960fdbfc8a; eftertest exit0; Worker STOPPED/slot null/board Done9 |
| Samlad verifiering | build/test/acceptans tre exit0, faktisk handoff f079d92b |
| Coordinator-review |39246d45-5cc2-4ee2-9bfb-3f1321ce4982, current;3/3 faktiska läskvitton/17595 byte |
| Begränsad permit |8b8dba76-446e-4853-88ba-e791b06101b1 efter separat exakt användarbeslut |
| Epic→main | exakt en no-ff6aab460d228be40a8a0aed36ba9cdfe55d11ac44; parents b4919eb1/60338838 och faktisk F08-marker |
| Integrationstopp | ursprunglig SID01a143e0-68bb-7b33-a484-8761fc3e63b1 fysiskt STOPPED genom F13 |
| Sluttest |740c15df-0ef0-41bc-9546-b2046a87babf, exit0 på exakt verklig mainmerge |
| Board | provepic Done4; synk6ed994ff-92e2-4a7c-adee-357a6fd2f412 |
| Repeat | två verkliga native MCP-anrop, identiska DONE/RELEASED-kvitton och ID:n; en merge/sluttest/stopp |
| Fortsättning | WAIT/RUNTIME_SECURITY_GATE_UNVERIFIED; andra epic/task Planned, ingen ny run/slot |
| Avslut | Worker/Integration/Coordinator STOPPED; egen server stoppad, PID1615840 frånvarande; refs/worktrees/state bevarade |
| Källor | samtliga83 produkt-/55 verifieringsbindningar identiska genom fysisk stängning |

Provet använde en genuin manuellt F34-registrerad produktkörning med explicit
F39-attachment, inte en fabricerad F38-claim eller adopterat utvecklingsworktree.
Därför är released_claim_id faktiskt null. Full positiv autonomi intygas inte.
Positiv två-epic-fortsättning från uppdaterad main, inklusive korrigerat medlemskap
samt förlorat startsvar, verifieras separat med riktig temporär Git/SQLite och
kontrollerade native/board/security-adaptrar. Generell OS-grind hör till F41.

## Manuella ingrepp och avbrottsavstämning

Journalförd Trust i ofarlig provmapp, Skip i egna nya uppdateringsdialoger för
bibehållen Codex0.162.1, egna scoped MCP-sessionstillstånd och en engångsgodkänd
Worker-git add/commit efter branch/path/common-dir/HEAD/index-/bytekontroll.
Separat skyddad mainpermit efter faktiskt användarbeslut. Inga globala Always
allow-val eller generell positiv säkerhetsattest. Native agenter fattade sina
riktiga task-/review-/leveransbeslut genom registrerade tjänster.

F34/Worker-start, för tidig review och ett ofullständigt kriterieförslag avstämdes
av samma tjänster/sessioner. Rootstoppets första okända utfall återlästes som
EXIT_PENDING; samma F13-nyckel fullföljdes efter verklig processinaktivitet. Ett
observer-only försök att skriva ut CoordinatorRun.result gav AttributeError efter
att korrekt STOPPED-kvitto redan sparats; produkten ändrades inte och inga effekter
upprepades. Alla faktiska journaler och slutliga processbevis finns kvar.

## Utvecklingsleverans

233 unika relevanta utvecklingsfall är verifierade över tre kvalificerade segment,
[exakta källor/nod-ID:n/kommandon](../reviews/F-40-current.json). Lint/build/READMECLI,
exakt wheel74moduler/sexpolicies och faktisk stdio-katalog Root6/Integration16/
Worker0 är verifierade. Utvecklings-F40 är nu Done3/3 efter granskad44cb0ef/no-ff f4d6621 och35
eftertester PASS1263.82s; [slutunderlag](../reviews/F-40-final.md). E10 kvar
Active2/3 tills F41/samlad leverans är verifierad.
