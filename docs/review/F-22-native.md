# F-22: verklig review, korrigering och taskleverans

## Avgränsning

Operatören driver produktservices sekventiellt på ett användargodkänt ofarligt
repository. Detta är ett produktprov med separata IDs, inte delegering av
HerdrCoordinators implementationsbacklogg. Högst en Worker är aktiv. Native
Workers implementerar och rapporterar; operatörens Integration-roll granskar
enligt [policy version 1](../../prompts/integration-review-v1.md). Den långlivade
Integration-runtime kommer först i E-09.

Fixture-main ska vara oförändrad. Första draften har ett fördeklarerat fel:
`value.split()` kan ersättas av en str-subklass och förlora den underliggande
Unicode-texten. Worker redovisar begränsningen; dess första lokala tester
bevisar vanliga strängfall. En separat oberoende regression reproducerar felet
innan CHANGES_REQUESTED. Ingen fullacceptans eller approval påstås för draften.

En separat task startas efter den första taskens verifierade Done och fysiska
stopp. Operatörens fasta verifieringskommando har en fördeklarerad otillgänglig
extern förutsättning för denna task och returnerar faktiskt exit 23. Lokalt
passande unittest-resultat ersätter inte denna verifiering. Inget READY, merge
eller Done får registreras för det blockerade resultatet. Runtime stoppas genom
F-13 med bevarad branch, worktree, session och operationshistorik.

## Återkörning och evidens

Harness: [scripts/probes/f22_native.py](../../scripts/probes/f22_native.py).
Kör från aktuell task-worktree med README:s låsta miljö. `prepare` kräver ett
nytt privat `.herdr/probes/f22` och ett separat befintligt, godkänt repository.
Starta först en ny namngiven testserver; använd aldrig användarens huvudserver.
`HERDR_ENV=1` krävs. Servern får stoppas först efter faktisk inaktivitet för
samtliga Workers som provet äger.

```sh
herdr --session hc-f22-20261008 server
PYTHONDONTWRITEBYTECODE=1 uv run --locked python scripts/probes/f22_native.py prepare --repo /absolute/trusted/fixture
PYTHONDONTWRITEBYTECODE=1 uv run --locked python scripts/probes/f22_native.py start
```

Återläs `start` med samma immutable specifikation tills parent är SUCCEEDED.
En observerad prompt/WORKING-ACK är inte ensam en slutförd F-15-start. Ingen
ny prompt skickas blint vid timeout eller okänt utfall. Därefter, när faktisk
native turn är avslutad och Worker idle/done:

```sh
PYTHONDONTWRITEBYTECODE=1 uv run --locked python scripts/probes/f22_native.py initial
PYTHONDONTWRITEBYTECODE=1 uv run --locked python scripts/probes/f22_native.py changes
PYTHONDONTWRITEBYTECODE=1 uv run --locked python scripts/probes/f22_native.py corrected
```

Återläs `changes` med samma nyckel för dess korrelerade ACK. Inspektera den nya
fullständiga kontextens diff, källor, kriterier och oberoende tester före
`approve`. `approve` är ett operatörsbeslut, inte automatisk LLM-review. Sedan:

```sh
PYTHONDONTWRITEBYTECODE=1 uv run --locked python scripts/probes/f22_native.py approve
PYTHONDONTWRITEBYTECODE=1 uv run --locked python scripts/probes/f22_native.py deliver
PYTHONDONTWRITEBYTECODE=1 uv run --locked python scripts/probes/f22_native.py start --blocked
PYTHONDONTWRITEBYTECODE=1 uv run --locked python scripts/probes/f22_native.py collect --blocked
PYTHONDONTWRITEBYTECODE=1 uv run --locked python scripts/probes/f22_native.py stop-blocked --blocked
PYTHONDONTWRITEBYTECODE=1 uv run --locked python scripts/probes/f22_native.py export
```

F-21:s stabila leveransnyckel återanvänder faktisk merge/test/stopp. Worktree
behålls; inget automatiskt cleanup utförs. Full privat evidens i det ignorerade
probdirectoryt innehåller riktiga runs, immutable specifikationer, native
ACK/report-provenance, två reviews, Git-SHA och tester. Publicerad evidens ska
vara explicit vald och sanerad, aldrig rå session-/process-/agentkontext.

Det frivilliga exakta Daybreak setup-bannret kan stängas med dess dokumenterade
Esc. En riktig approval/trust/question-dialog kräver operatörens beslut; harness
besvarar den inte. F-17:s autonomi-/sandboxgate inför F-38 består. Native
Workers anropar inte produkt-MCP direkt i detta operatörsdrivna fas-5-prov.

## Faktiskt resultat

Native-provet genomfördes 2026-10-08 med Herdr 0.9.3. [Sanerad evidens](F-22-prover.json).

- F-22.A1: initial commit `9a7bc990db31b8ea2e3221c515e929d62f09e01c`,
  negativ review `e9826cf2-a6be-44be-8a37-5d4642832c91`; samma session
  `01a11c29-16f7-73f1-a963-a45ffdb22ad3`, branch och worktree. Oberoende
  regression exit 1 → 0; korrigerad commit
  `009f3dd033dc9ff07daaca79ca79e5d7735ac3ed`, tio tester, nytt native handoff
  och context. Positiv review `f7533833-3129-4b6d-ade4-70761ab31d31` efter
  full diff-/käll-/kriteriegranskning, exakt epicbas
  `e90b68ce91ab84a07edf98798121726af90e95e2`.
- F-22.A2: faktisk --no-ff-merge `77abb664a6077986d9b12956360aa6909484b7cc`
  med parents (epicbas, korrigerad commit). Självständigt merge-test
  `d00fb45d-7bd6-4317-b262-78f57864f57c`, exit 0. Fysiskt stopp
  `e16b9614-01db-436a-9f75-7672c593d867` bekräftat före slotrelease/Done.
  Replay gav EXISTING, en merge. Fixture-main före/efter
  `7ce621ade256d1f7f6e35d94c9a6348ee21e87fe`.
- F-22.A3: separat Worker/run `14faff65-6d6e-4278-b94c-adcf69b46389`,
  source `54ec83027ed22724d54b8f37a4a5629dab91b623`. Verklig operatörstestprocess
  returnerade exit 23, operation `b2063920-be8d-4673-a7ad-35e170261171`
  FAILED/TEST_FAILED. Worker-report nekades REPORT_TEST_FAILED; samma-key replay
  gav REPORT_VERIFICATION_FAILED utan extra test. Ingen review, leveransmerge
  eller Done. F-13-stopp `02f8055c-8533-4091-a029-1aa0d0131209` bekräftade
  inaktivitet före slotrelease; PARKED/Attention med bevarade resurser.

Den första evidensexporten använde get_operations utan obligatoriskt kind och
fick TypeError efter att produktens verkliga negativa testutfall redan sparats.
Harness rättades och samma resultat återlästes; inga produktstatusar, tester eller
native rapporter ändrades. Syntax/lintfynd i harness korrigerades före leverans.
Båda Workers bekräftades fysiskt inaktiva före stopp av endast den ägda testservern.
F-22:s egen bootstrap review, Task → Epic-integration och relevanta grindar
återstår innan backlogg-/TeamPlayer-Done.
