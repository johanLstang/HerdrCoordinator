# F32: sparad input och samma-session-resume

En registrerad Integration-anslutning lämnar ett explicit `InputDecision` till
`resume_task` eller aliaset `worker_resume`. Båda kräver endast project/task-mål
samt ett strikt beslut: version1, UUID `input_id`, UUID `blocker_id` och ett
meningsfullt svar (högst8192 tecken, ingen NUL). Okända fält och roll-/slotbevis
avvisas. Svaret är operatörsinput; det ger aldrig Git- eller reviewapproval.

## Beständig kedja

1. `task_resume` sparar oföränderlig aktör, svar, F31-subjekt, blockerarhash,
   faktiskt historiskt parkstop, återupptagningsfas och unik korrelation före
   externa kontroller och runtimeeffekter. Källhash och faktisk statehistorik
   valideras på nytt. Ett annat svar på samma input-ID eller ytterligare svar
   till samma blockerare avvisas. Ny input hör till en ny blockerare.
2. F05/F13 kontrollerar registrerat worktree, branch, pane, server, agent och
   samma Codex-SID. Faktisk Git-HEAD sparas efter beslutet och kontrolleras före
   resume och enda dispatch. Saknad session eller flyttat worktree ger fel med
   beslutet kvar; ingen ersättningsrun/session skapas och kod raderas inte.
3. F13 reserverar slot atomiskt före fysisk resume. Full kapacitet lämnar
   `WAITING_RESUME`, svar och Attention beständiga. Verifierad fysisk resume
   uppdaterar startgeneration/processbindning men lämnar PARKED/Attention.
   F13 läser den verkliga väntande inputjournalen; ingen callerflagga kan
   välja detta eller kringgå ACK genom generiskt lifecycle-resume.
4. `DISPATCH_REQUESTED` med exakt prompt/hash, baseline-turns och45s deadline
   sparas före enda sändning. Native userMessage och exakt agentMessage-ACK
   ska finnas i samma nya turn med rätt input/blocker/korrelations-ID, SID,
   worktree och verifierad runtimegeneration. Först därefter återställs sparad
   fas genom `input-ack:<operation>`. WORKING respektive REVIEWING återställs
   utan ny task/session/slot eller implicit reviewapproval.
5. F25 verifierar input/ACK/resume-källan och publicerar Active. Nätfel efter
   ACK lämnar `CONFIRMED` och återförsöker bara synk. F25 visar Attention
   när input väntar på kapacitet/ACK, även om sessionen är fysiskt startad.
   Input-ID/hash skapar ny historikrevision utan att ändra gamla F31-nycklar.
6. F29:s begränsade tick driver befintlig verifierad inputjournal under sin
   ursprungliga Integration-principal med färsk board. Väntande ACK/kapacitet
   hindrar inte andra tasks. Offline tick återparkerar inte en F32-resumad
   PARKED-generation och skickar ingen input. Ingen dold autonom loop eller
   inputleverantör införs.

F16 tar nya rapporter först efter senaste bekräftade input-/korrigerings-ACK.
En gammal BLOCKED-rapport före svaret får inte återblockera återupptaget arbete.

## Okända och partiella resultat

Samma input-ID återanvänder journal och ursprunglig resumeavsikt. Okänt native
resume startas inte blint igen; känd effekt observeras. Förlorad checkpoint efter
fysisk resume återanvänder faktisk F13-success. Okänd promptleverans observeras
utan omsändning. ACK-timeout behåller svar, slot och fysisk runtime med konkret
`INPUT_ACK_TIMEOUT`; ansvarig Integration ska kontrollera aktuell native turn
eller stoppa genom F13 före release. Ingen timeout fabricerar inaktivitet.

En återöppnad databas bevarar väntande input, prompt, ACK och synkavsikter.
Beslut/resultat/loggar innehåller inga credentials. MCP-resultat visar IDs,
steg och fasta felkoder, aldrig det råa svaret eller upstreamfel. Journalen
är beständig operatörshistorik; håll den privata SQLite-filen skyddad.

## Faktisk verifiering

Serviceprov kör verklig temporär Git/SQLite/F05/F15/F16/F31/F13/F25-kedja med
kontrollerad runtime/board. Nativeprovet återanvänder F31r2:s verkliga registrerade
run och fysiskt stoppade SID `01a1212b-1132-7f13-aa40-e3af437b129e` i samma
branch/worktree. Operatören lämnar explicit ofarligt fixturevärde
`RETENTION_DAYS=7`; detta är ingen produktpolicy. Input sparas, slot reserveras,
F13-resume21115792 följs av faktisk korrelerad ACK, och native task blir
InProgressv9/User105. Replay har en start, assignment, resume, input och blockerare.

[Maskinföljbart nativeunderlag](F-32-prover.json). Första native resume/export
passerade; aktuellt source-/mergeunderlag redovisas vid taskreview. Fullt scenario
med A Attention och B/C fortsätter samt högst två faktiska Workers levereras i F33.
F38/F47:s autonomigate/full recovery ingår inte. F31:s tidigare underkända
startförsök och gamla refs/worktrees/journaler bevaras.

## Sourcegrindar

- `uv run --locked pytest tests/test_task_resume_service.py -x` — 23 PASS78.89s/exit0.
- `uv run --locked pytest tests/test_task_scheduler.py -x` — 14 PASS238.14s/exit0.
- `uv run --locked pytest tests/test_runtime_lifecycle.py tests/test_task_attention_service.py tests/test_worker_report_service.py tests/test_teamplayer_sync.py tests/test_mcp.py -x` — 130 PASS455.13s/exit0.
- Ruff/build/configCLI/diff, 56 exakta wheelmoduler, två force-include-promptpolicies och117 länkar PASS.

Serviceproven täcker strikt korrelation, dubbelinput/resume, oföränderligt svar,
fulla två reservationer, start/resume-race, återöppnad databas, avbrott efter
fysisk resume, okänd runtime/promptleverans, timeout, saknade resurser, ändrad
HEAD/generation/journal, roller, aktuell rapportgräns, reviewfas utan approval,
SDK-schema och bara saknad synk vid nätfel. Regressionerna separeras från
verkligt nativeprov. Slutlig review och integrationskontroller redovisas separat.
