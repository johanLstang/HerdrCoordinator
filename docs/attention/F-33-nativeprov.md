# F33: Attention medan andra tasks levererar

## Leverans och roller

Detta är ett avgränsat prov mot verklig Herdr, Codex och TeamPlayer. Produktens
services validerar taskägare, slots, native rapporter, Git, review, tester och stopp.
Testoperatören utför Coordinator- och Integration-rollerna sekventiellt. De tre
Workers som körs i provet är produktfixtures; utvecklingen av backloggen sker
fortfarande en feature i taget.

A, B och C är oberoende. A måste fråga efter det avsiktligt saknade RETENTION_DAYS
utan att ändra filer. B implementerar intervallintersection och C normaliserade
unika Unicode-taggar, med egna moduler och unittest. C behöver inte A:s kod.
Operatören lämnar det uttryckliga fixturebeslutet RETENTION_DAYS=7. Det är
testdata inom A:s scope och ger inget godkännande av implementationen.

## Se Attention och lämna input

1. Läs den verkliga tasken med TeamPlayers `get_task`. `NeedsInput` visas i
   Attention. Beskrivningen innehåller orsak, konkret inputbehov och ansvarig roll.
   Epicen behåller Active. Läs även runstatus: PARKED betyder bevarad
   session/branch/worktree; `worker_slot=null` räknas som frigjord först efter
   F13:s verifierade fysiska stopp. Kanban ensam bevisar inte inaktivitet.
2. Läs taskens F31-journal och använd dess faktiska blockerar-ID. Lämna ett
   uttryckligt versions-1-beslut till Integration via `resume_task` eller
   `worker_resume`, med exakt `task_run_id` och `decision`:

   ```json
   {
     "version": 1,
     "input_id": "<nytt UUID för just detta svar>",
     "blocker_id": "<faktiskt F31-operation-ID>",
     "answer": "<det beslutade svaret inom taskens scope>"
   }
   ```

   Exemplet är en mall, inte ett giltigt beslut. Behåll samma input-ID och
   oförändrat svar vid retry; byt inte ID för att kringgå ett okänt resultat.
   Saknas extern information stannar tasken i Attention.
3. Med båda platserna upptagna sparar F32 beslutet och visar WAITING_RESUME.
   Tasken ligger fortfarande i Attention. En begränsad scheduler-tick driver
   senare samma journal när kapacitet finns. Ingen ersättningstask eller session
   skapas. Den uttryckliga operatorharnessen aktiverar ingen dold produktloop.
4. F13 reserverar en ledig slot före samma-session-resume. F32 levererar svaret
   en gång och kräver korrelerad native ACK innan WORKING/Active och TeamPlayer
   InProgress. Fysiskt återupptagen session kan tillfälligt fortfarande vara
   PARKED/Attention medan ACK eller extern synk saknas; reservationen kvarstår.
5. Vid okänt resume-/transportresultat: läs operationerna innan retry. Vid
   ACK-timeout eller saknad session/worktree: bevara input/resurser/reservation,
   dokumentera fel och nästa åtgärd. Generisk resume eller en ny Worker är ingen
   tillåten genväg. Se [F32-kontraktet](F-32-aterupptagning.md).

## Köra det avgränsade provet

Kör från det egna F33-worktreet med README:s låsta uv-miljö. `HERDR_ENV=1` krävs.
Ange en separat, redan godkänd ofarlig reporot och en ny server `hc-f33-*`.
Harnessen använder F05 för nya registrerade produkt-worktrees och sparar
privata artefakter i `.herdr/probes/f33`. Den adopterar inga bootstrapworktrees
och återanvänder inga gamla run-/fixture-ID:n.

```bash
uv run --locked python scripts/probes/f33_native.py prepare --repository /absolut/godkänd/testrepo --server hc-f33-egen
herdr --session hc-f33-egen server
```

Starta den egna servern i separat terminal. Skapa därefter en separat testepic och
tre tasks i det verifierade TeamPlayer-projektet med exakt `specs.json`-namn och
acceptans, inga inbördes beroenden och User-tilldelning till det autentiserade
kontot. Spara serverns **återlämnade** epic-/task-UUID:n i `fixture.json` med
`projectId`, `epicId`, `tasks` (A/B/C) och `userId`. Använd inte exempel-ID:n.

```bash
uv run --locked python scripts/probes/f33_native.py bind
uv run --locked python scripts/probes/f33_native.py drive --seconds 45
```

Varje drive är en begränsad observation av samma sparade starter och deadlines.
Om start/ACK fortfarande väntar fortsätter just den observationen över det angivna
pollfönstret, högst ytterligare 225 sekunder för de tre ursprungliga starterna.
Ingen F15-deadline förlängs och ingen prompt skickas igen.
Fortsätt bara kända journaler efter återläsning. Upprepa inte Gitpreparation,
assignmenttransport eller native start. Harnessen avbryter vid PAUSED och bevarar
de faktiska delresultaten. Startupvakten tillåter inte att assignmenttext klistras
in i ett trust-, uppdaterings-, konto- eller behörighetsdialogfönster.

När A är faktiskt parkerad och B/C har slot 1 och 2, skapa en explicit
`input.json` enligt mallen ovan med faktisk blockerare och fixturebeslutet.

```bash
uv run --locked python scripts/probes/f33_native.py input --decision-file /absolut/input.json
uv run --locked python scripts/probes/f33_native.py review --task B
```

Läs **hela** aktuella `review-B.json`, dess krav/källor/testresultat och
`review-B.diff`. Skriv ett separat explicit `ApprovalDecision` enligt
[F20](../review/F-20-godkannande.md) först efter faktisk review, med exakt
context-ID och samtliga verifierade kriterier. `deliver` skapar inga beslut.

```bash
uv run --locked python scripts/probes/f33_native.py deliver --task B --decision-file /absolut/approval-B.json
uv run --locked python scripts/probes/f33_native.py review --task C
uv run --locked python scripts/probes/f33_native.py deliver --task C --decision-file /absolut/approval-C.json
uv run --locked python scripts/probes/f33_native.py resume --seconds 45
uv run --locked python scripts/probes/f33_native.py review --task A
uv run --locked python scripts/probes/f33_native.py deliver --task A --decision-file /absolut/approval-A.json
uv run --locked python scripts/probes/f33_native.py export
```

C och A kräver varsin full aktuell review och egna beslut precis som B.
Kör ingen scheduler-tick mellan de explicita B/C-leveranserna: A:s sparade
beslut väntar medan Integration avslutar båda. Därmed provas faktisk B/C-Done
med A fortfarande PARKED/NeedsInput. Efteråt driver schedulern den befintliga
inputjournalen; ingen direkt SQL-stateändring eller agentens egna mergepåståenden
används. Vid READY_FOR_REVIEW direkt efter resume måste faktisk efter-ACK-rapport
observeras och granskas innan leverans, som vanligt.

`export` kontrollerar aktuella Gitparents, registrerade leveransbevis, faktiska
eftertester, fysisk inaktivitet, User/Done-readback och bevarade gamla resurser.
Den återkör bara kända input-/scheduleravsikter för att kontrollera att inga
sidoeffekter dubbleras. Den lokala tidslinjevalidatorn avvisar motsägande eller
ofullständiga artefakter; den autentiserar inte godtycklig JSON och ersätter inte
services eller verkliga native/Gitkontroller.

Stoppa endast den egna namngivna servern efter bekräftad inaktivitet för alla tre
Workers. Bevara refs, worktrees, SQLite och operatörsartefakter. Fixtureepicen
förblir Active eftersom provet inte integrerar den till fixture-main. E08:s
utvecklingsleverans går separat genom full review, PR, mainmerge och sluttester.

## Verifieringsunderlag

F33.A1 kräver B/C-Done medan A är parkerad; A2 kräver sparad input vid fulla
slots och samma-session-resume efter ledig kapacitet; A3 kräver tre verkliga
granskade/integrerade/testade/stoppade Done och högst två Workers. Native
tidslinje och resultat sparas efter utfört prov. Kontrollerade validatorprov
redovisas separat från verklig extern integration.

### Underkänt första försök och nytt prov

Försök 1 missade C:s ursprungliga ACK-deadline efter att harnessen lämnat en
väntande start. Det räknas inte som PASS. A/B/C har faktiska park-/stoppbevis,
User105/NeedsInput och slotnull; egen första server är stoppad. Alla resurser
bevaras. `--attempt 2` använder nya separata F05/run/native-ID:n och server;
läs och bevara första försökets `abandoned-attempt.json`. Lägg samma flagga
på varje r2-kommando, med beslut och artefakter från `.herdr/probes/f33-r2`.

## Faktiskt resultat 2026-10-09

Verklig r2-fixtureepic `61b8f12d-0eb2-412a-afea-d524af6819c6`, lokal
run `f33-epic-run-r2`, egen server `hc-f33-r2-20261009`. Alla native tasks
har User105/blitterbot som utförare. Fixtureepicen är Active; fixture-main
förblir `d4215a26ba5e77d64a1fd4aed4117e5313c43563`.

| Fas (UTC) | Faktiskt resultat |
| --- | --- |
| 16:18:26 | A PARKED/NeedsInput; B slot2/C slot1; sparad input, ingen resume. |
| 16:21:12 | B Done efter merge/test/stopp; A kvar PARKED/NeedsInput. |
| 16:22:50 | C Done efter aktuell review/merge/test/stopp; A kvar PARKED/NeedsInput. |
| 16:23:58 | A WORKING/InProgress efter samma-session-resume och native input-ACK. |
| 16:25:37 | A Done efter aktuell review/merge/test/stopp; alla slots null. |

Parkstop `98505af4-9b37-4051-a593-d64744a63489` föregick C:s claim.
F31-blockerare `b42347ef-28b1-4867-af12-54e9e20fae38`, sparad input
`921217b8-2632-49c3-ba0b-4314d479d8da`, faktisk F13-resume
`a6b30a1a-547b-402c-a29a-6797309beb54`. A:s SID är oförändrat
`01a12174-220e-7d90-8b86-fe9362c6167f`; branch/worktree/run bevaras.

| Task | Native Done-version | Faktisk Task → Epic-merge |
| --- | --- | --- |
| B | 7 | `278e2b0a004433978800818999104099364cfa03` |
| C | 7 | `c300ee7affd437d203d3429fb0cb41b1331ba6a3` |
| A | 10 | `3f8326b3a2977738a8bfa0eaae95db634102a3c3` |

78 native observationer, inklusive 12 tidsbracketerade samtidiga B/C-Working-prov
med A parkerad, visar högst två reservationer/native Workers och separata
sessioner/worktrees/processidentiteter. En observation var otillgänglig och
redovisas; den räknas inte som bekräftad. Varje leverans har full aktuell
explicit review, exakta no-ff-parents, verifierat eftertest och fysiskt stopp.
Replay i Active gav ingen extra input/start/sändning; resume efter Done nekades
med INPUT_SCOPE_DENIED utan effekt. Slutligt scheduler-replay gav tre Done
utan nya merges. Gamla refs/worktrees och andra boardobjekt är bevarade.

Den första exportavstämningen flaggade den redan dokumenterade, behöriga
uppdateringen av själva utvecklings-F33:s beskrivning från v3 till v4 under
provet. Exakt task-ID, versionspar och båda beskrivningshasharna binds separat;
bara denna kända text/version får skilja sig. Status, tilldelning, nya/raderade
tasks eller andra epicändringar kan inte döljas av undantaget. Native fixture-
skrivaren har aldrig behörighet till utvecklings-F33. Den inledande stoppade
exportkontrollen redovisas som underkänd kontroll, inte som ett nätfel eller PASS.

[Maskinföljbart underlag](F-33-prover.json) innehåller relevanta faktiska
fält; fulla contexts, diffs, prompts och SQLite-historik bevaras privat. Den
lokala validatorgrinden och Herdr-startupvakten:
`uv run --locked pytest tests/test_f33_timeline.py tests/test_f30_timeline.py -x`
— 51 PASS/1.16s/exit0. Detta är kontrollerade artefaktprov, separat från det
verkliga native scenariot. Task-Done kräver fortfarande egen utvecklingsreview,
Task→E08 och eftertester. E08-Done kräver dessutom PR/mainmerge/slutverifiering.
