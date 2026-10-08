# F-19: verklig review och korrigering i samma session

Provet den 2026-10-08 använder det av användaren betrodda F-10-repositoryt och separata produkt-ID:n i [bevisfilen](F-19-prover.json). Herdr-session `hc-f19-20261008` är skapad för provet; default-sessionen påverkas inte. Integration-beslutet tas av den sekventiella operatörsrollen, inte en autonom Integration Agent från E-09.

## Verifierat förlopp

1. F-15 startar `f19-label-second-attempt`, run `afe1ebb6-890c-4b95-8d23-4ec86ff5d94c`, med slot före start, riktig native ACK och komplett SUCCEEDED-parentjournal. Worker `hc-d9ad7e4310f241b3bb92bb37` använder Codex-session `01a11bb4-60bb-7fe1-8987-c9bf8e00cb4c`, pane `w2:p1` och egen task-branch/worktree.
2. Worker levererar `16fd50fbb76d10d80f86f8862d6541109dd693c1` med sju tester. F-16 kör operatörens unittest-kommando oberoende; F-18 kör en ny verifiering och sparar komplett context `c707eab4e9502808cef912b6866d11a9ca56abddd9db71768e3c74393432d9f6`.
3. Granskningen reproducerar ett konkret fel: en accepterad `str`-subklass kan överlagra `split` och ersätta sitt Unicode-innehåll. Review 1 kopplar problemet och avgränsad fix till befintlig Unicode-acceptans, task-SHA och epic `288b85d6f130e504ae1d0f7043979b6287abf378`. F-19 sparar Review före prompt och får korrelerad native ACK från samma session innan CHANGES_REQUESTED → WORKING. Operation `cababba0-a603-4ab6-a292-cafd116fada4` levererar exakt en korrektionsprompt.
4. Samma Worker skriver regressionsprov som först reproducerar felet, korrigerar kod och committar `2b26b470ee8aac5c222479af2000dab1fa291b18`. Nio tester passerar. En oberoende kontroll av subklass som överlagrar både `split` och `__str__` passerar utan bytecodeskrivning.
5. Den första korrigeringsrapporten blandar historiskt rött TDD-prov med aktuell testlista. F-16 avvisar den med REPORT_CLAIMED_TEST_FAILED; ingen failure filtreras bort. En explicit, journalförd rapportförtydligande prompt går till samma verifierade redo session. Ny finalrapport redovisar historiskt fel ärligt i sammanfattningen och endast aktuella tester i testlistan.
6. F-16 verifierar native rapport efter korrektions-ACK och kör nya oberoende tester på korrigerad commit. F-18 bygger nytt aktuellt context `6dafbc492e5a8081637b5bd115e4e3a8642738c102d86bf7141c955418552bf1`. Rapportproveniens innehåller korrektionsoperationens ID. Tasken når REVIEWING, håller slot 1 och har varken approval, merge eller Done.
7. Upprepning av samma korrektionsnyckel återläser EXISTING utan DB-ändring eller extra prompt. F-13 bekräftar fysisk inaktivitet före slotrelease. Provrun är därefter PARKED/Attention med resume_state REVIEWING, samma session/branch/commits och slot null. Den namngivna testservern stoppas; historik och worktrees bevaras.

## Bevarade misslyckade försök och gränser

Det första separat namngivna försöket, run `28b490c6-c071-49f0-96aa-8a63d03ae2e4`, missade F-15-parentcompletion när harness observerade F-12 direkt och gick vidare till F-16. F-18 avvisade korrekt ofullständig startjournal. Försöket stoppades genom F-13 och sparas PARKED/inaktivt; det återställs inte och ersätter inte någon implementationstask i TeamPlayer.

Operatörens fristående felreproduktion skapade två otrackade Python-bytecodefiler. Git-kontrollen avvisade smutsigt underlag. Endast dessa exakt kända operatörsskapade filer sparades separat och togs bort; Worker-kod och historia bevarades. Senare kontroller använder PYTHONDONTWRITEBYTECODE=1. Ingen broad cleanup, reset eller falsk framgång används.

Git, SQLite, Herdr/Codex, native prompt/ACK, Worker-fix, commits och testprocesser är verkliga i detta prov. Worker anropar inte MCP direkt; roll/strikt input provas separat genom riktig MCP-klient/server med kontrollerad runtime. Fullt säkrad filsystems-/Git-/testsandbox har inte omverifierats här. F-17:s explicit dokumenterade autonomigate inför F-38 kvarstår. Ingen produktapproval, leveransmerge eller task-Done påstås av detta avgränsade F-19-prov; hela E-05-flödet tillkommer i F-22.

Detaljerade, oförändrade operations-/context-/rapportbevis finns i den ignorerade harnessmappen `.herdr/probes/f19` i implementationsworktreet task-e05-f19. Den publika JSON-filen innehåller identiteter, revisions-/testreferenser, SHA, deduplicering och stoppbevis utan credentials.
