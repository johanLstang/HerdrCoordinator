# F30: tre tasks med två verkliga Workers

## Verifierat resultat

Native Herdr 0.9.3/protokoll 22, Codex CLI 0.162.0, verklig TeamPlayer-MCP,
Git-worktrees och SQLite provades 2026-10-09. A/B hade 15 faktiska, bracketade
`working`-observationer med olika worktrees, Codex-sessioner och processer.
Alla 28 domänövergångar kontrollerades: högst två unika slotreservationer,
och C:s claim kom efter granskad, mergad, testad och stoppad A-Done.
[Maskinföljbart underlag](F-30-prover.json) innehåller exakta ID:n, events,
approvalhistorik, merge-/test-/stoppbevis och första native överlappningen.

F29:s scheduler valde, reserverade, startade, observerade, integrerade och
synkade alla tre tasks. Operatören gav fyra faktiskt granskade F20-beslut;
inga individuella taskstarter, manuella schedulingbeslut eller automatiska
godkännanden infördes. A/B godkändes först mot samma epicbas. Efter A-merge
återköades B:s gamla approval, hela tasken synkades/testades och nytt aktuellt
granskningsbeslut krävdes. B behöll sin Worker-session och slot.

| Task | Externt task-ID | Godkänd source | Faktisk no-ff-merge |
| --- | --- | --- | --- |
| A / Unicode clean_name | `09c97bc4-f5f8-47c1-989f-5c21b6ff0c3d` | `c03a7f37b0193bc4de3b107e9ed240b72ab94413` | `637bd2edffee9c8697596999b7655d2ca86a1457` |
| B / validerad sum_even | `f3459a10-1a4e-4af4-a01c-0928adc03f5f` | `42528a4c0765bd9d14f561dafc25d2b0b9defad8` | `78e28152649c6bb11d73532537dd8a81bce8d90d` |
| C / label genom A | `bd0bb840-3f4e-4881-b85b-baab96e4f741` | `77bd24ef2ed343ac3c489c29b663fbb696aac0d2` | `6308fb602aec1b6fe2c60a22b1fde2ae81d4408b` |

Alla leveranser hade oberoende tester på aktuell source/target och faktisk
merge, samt F13:s fysiska stopp före slotrelease/Done. Worker-testsviterna
och den separata verifieraren kontrollerade Unicode/NFC, tomma namn, fel
typer, negativa/tomma listor, bool-avvisning, oförändrad input och C:s
användning av integrerad A. Historiska greeting-filer och tester bevarades.

Native tasks återlästes Done/version 10, 11 och 8, med `User` och verifierad
blitterbot@gmail.com. Testepicen `840d61d5-1840-4041-bb17-0371c9410585`
förblev Active/version 5. Dess mainintegration ingår inte i F30.
Fixturmain `d4215a26ba5e77d64a1fd4aed4117e5313c43563` och gamla refs/worktrees
förblev oförändrade. Full F24-baslinje visade alla andra boardobjekt exakt
oförändrade vid export. Senare metadata för utvecklings-F30/E07 redovisas
i deras egna leveransunderlag.

Ett extra scheduler-tick efter Done gav samma tre runs/sessioner/commits,
utan extra runtime, assignment, merge, leveranstest eller stopp. Båda egna
testservrarna stoppades först efter kontroll av egna inaktiva processer.
Den ordinarie Herdr-servern berördes inte. Alla journaler/worktrees bevaras.

## Kända delresultat och underkänt första försök

Förberedelsen skapade faktisk F05-epic och seed `cb1d80f2739d8b8c89391dbdeed91d76d056db48`,
men använde fel metadata-API. StateStore avvisade commitfält i runtime-API:t.
Efter full kontroll av ägare, path/branch, bas, exakt HEAD/parent, komplett
ren AGENTS/README-diff och gamla refs kompletterades bara metadata/checkpoint.
Ingen andra seed-commit eller ersättningsrun skapades. Commitfält uppdateras
med `update_run_metadata`; runtimefält med `update_runtime_metadata`.
Samma API-fel rättades i approval-requeue och provades med faktisk ny Git-HEAD
innan epicmetadatan ändrats: gamla pins rensas, reviewhistorik/session/slot
bevaras och ett nytt aktuellt granskningsunderlag krävs.

Första nativeförsöket `.herdr/probes/f30` är underkänt. A:s assignmenttransport
träffade en Codex-uppdateringsdialog; panen visade `npm install -g @openai/codex`
och exit efter uppdatering. Ingen verifierad ACK/session eller taskleverans
fanns för A. B implementerade men ACK-deadline löpte ut under felsökningen.
B stoppades/parkades genom F13; A:s gamla slot behölls konservativt. Efter
kontroll av gamla processidentiteter, tom agentlista och enbart shell i
egna panes stoppades endast den egna servern. Gamla journaler, kod, session,
branches och worktrees skrevs inte om för att låtsas lyckad recovery.

Det lyckade provet är ett separat, sekventiellt F05-prov under
`.herdr/probes/f30-r2`, med nya lokala project/epic/run/branch/worktree-ID:n
och `hc-f30-r2-20261009`. Samma godkända reporot och externa fixture-ID:n
användes medan tasks fortfarande var Pending; ingen boardrollback eller
ersättning av task-ID gjordes. Detta är manuell testoperatörshantering,
inte F47:s framtida recoverypolicy och inte adoption av bootstrapworktrees.

Native transporten kontrollerar nu synlig UI före assignment. Den avfärdar
bara den kända frivilliga Daybreak-bannern med Esc. Update, trust, approval
eller okänd UI får inte ta emot assignmenttext. Drive fortsätter observera
en ännu obekräftad start inom dess ursprungliga deadline även om en annan
task pausas. Det ursprungliga underkända provet räknas inte som parallellitet.

## Köra och granska ett avgränsat prov

Harnessen [f30_native.py](../../scripts/probes/f30_native.py) körs från det
egna utvecklings-worktreet med `HERDR_ENV=1`. Den kräver en uttryckligen
godkänd, ofarlig Git-reporot med ren main, nya lokala provresurser och en
egen namngiven Herdr-server. TeamPlayer-credentials läses av betrodd adapter
och får inte skrivas till output, filer eller Workers kontext.

1. `prepare --repository <godkänd-reporot> --server <egen-hc-f30-server>`
   skapar nya F05-resurser och `specs.json`. Kör aldrig om detta mot befintlig
   journal. `finish-prepare --expected-seed <faktiskt-granskad-full-SHA>`
   kompletterar endast den ovan beskrivna kända seed-checkpointen.
2. Skapa separat testepic och tre tydliga tasks enligt specs, tilldelade
   verifierad User105, med C beroende av A. Spara faktiska UUID i `fixture.json`.
   Harnessen begränsar senare skrivningar till just dessa fyra fixture-ID:n.
3. Starta endast den egna namngivna servern och kör `bind`, därefter
   `drive --seconds 45` eller `tick`. Upprepa tick medan arbete pågår.
   Default är ingen implicit review/approval och ingen automatisk omstart.
4. Läs varje komplett aktuell `review-A/B/C.json`: source/target, specifikation,
   full diff, källor, kriterier och oberoende tester. Skriv först efter faktisk
   review ett strict F20-beslut till beslutfil och kör
   `approve --task A --decision-file <beslutfil>` (motsvarande B/C).
   Ändrad epicbas kräver nytt underlag och nytt beslut.
5. När alla tre Done: kör `export`. Samtliga F27-leveransbevis, slot/events,
   native överlappning och skyddad Git-/boardbaslinje måste passera.
   Kontrollera replay och faktisk inaktivitet före egen serverstopp.

Alla kommandon använder prefixet:

```bash
uv run --locked python scripts/probes/f30_native.py <phase>
```

Det bevarade andra försöket använder även `--attempt 2`. Återkör inte dess
prepare/bind och starta inte om dess stoppade server för att fabricera nya bevis.
CLI skriver fasta säkra fel; avstäm journalen efter okänt externt utfall.

## Utvecklingsverifiering

```bash
uv run --locked pytest tests/test_task_approval_service.py tests/test_task_scheduler.py tests/test_f30_timeline.py -x
uv run --locked ruff check .
uv build
uv run --locked herdr-coordinator --config herdr.example.toml --check
git diff --check
```

Slutlig källkörning: 49 PASS, 197.13 s, exit 0. Bygg/config/diff/Ruff PASS,
52 wheelmoduler och två rollpolicies exakt mot källan, 106 lokala länkar PASS.
Serviceproven använder verklig Git/SQLite med kontrollerad runtime/board;
nativeprovet ovan redovisas separat. [Faktisk leveransreview](../reviews/F-30.md)
registrerar Task→Epic f267306 och 122 eftertester PASS; utvecklings-F30 är Done.
E07 väntar på egen samlad slutreview och mainintegration.

## Begränsningar

Domänövergångarna är kompletta; native UI/processer observeras som bracketade
stickprov under servicevarven. Detta är inte kontinuerlig OS-monitorering.
Review är explicit operatörsarbete. Långlivade agentloopar, blockerarpolicy och
autonom recovery är senare features. F38:s miljö-/autonomigate kvarstår:
native funktionstestet bevisar inte OS-isolering eller fri produktionsautonomi.
F30:s utvecklingstask och E07 blir Done först efter sina egna granskade
integrationer och verifieringar enligt projektets arbetsprocess.
