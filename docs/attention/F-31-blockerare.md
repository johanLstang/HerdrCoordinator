# F31: blockerare och säker parkering

## Leveranskontrakt före implementation

En betrodd Integration-tjänst samordnar verifierad BLOCKED-rapport eller aktuell
F18-review som behöver extern input, beständig blockerarjournal, F25-synk och
F13-parkering. Worker rapporterar; Integration skriver status och parkerar.
Routingrollen User/Integration/Coordinator anger nästa ansvar och förhöjer ingen roll.

Journalen binds till verklig källa/context, startgeneration, registrerad run,
branch/worktree och Codex-session. Reason, konkret inputbehov och ansvarig roll
sparas före externa steg. Schema2 och v1-Worker-rapporten bevaras.

Attention/NeedsInput kan publiceras före bekräftad parkering. Kanban är inget
inaktivitetsbevis. F13 kontrollerar egna verkliga runtime-/processfakta och
frigör precis rätt slot först efter fysisk inaktivitet. Ett nätfel lämnar
F25:s beständiga synkavsikt och får inte hindra säker parkering.

Upprepning efter lyckad parkering använder tidigare faktiskt stoppbevis och
återförsöker bara saknad synk. Timeout, okänt utfall, främmande task eller ändrad
identitet frigör ingen slot. Ursprunglig session/branch/worktree och blockerad
fas bevaras för F32. Ingen ny run, automatisk input eller resume införs här.

Reviewinput binds vid skapande till senaste verifierade context och aktuella
source/target. En historisk blockerare ska fortsatt kunna speglas medan andra
tasks levereras; den är ett dokumenterat historiskt beslut, inte ett nytt
godkännande för merge. F32 återvaliderar fakta före återupptagning.

## Verifieringsplan

- F31.A1: fullständig blockerare med källa, orsak, inputbehov och ansvarig roll på rätt autentiserad User-task; både native Worker-rapport och reviewinput.
- F31.A2: faktisk F13-parkering före slotrelease, bevarad session/branch/worktree/commit och tidigare state.
- F31.A3: dubbel rapport/replay/återöppnad DB, främmande roll/task, stale review, ändrad runtimegeneration, timeout och nätfel utan osäkert frigörande eller ny runtime.
- Verkligt separat native prov med ny F05-run och avgränsade TeamPlayer-test-ID:n, tidigare godkänd ofarlig reporot och egen Herdr-server.

Serviceprov använder temporär Git/SQLite och kontrollerade board/runtimeadaptrar.
Native resultat redovisas separat med faktisk session/process/stop/slot/synk.
Gamla fixture-resurser bevaras. Bootstrapworktrees adopteras inte och F38:s
miljö-/autonomigate samt F47:s recovery kvarstår.

[Backlogg och acceptans](../../Backlog.md), [F13-livscykel](../runtime/F-13-livscykel.md),
[F25-synk](../teamplayer/F-25-synk.md) och [scheduler](../scheduling/F-29-scheduler.md).

## API och beständig journal

`TaskAttentionService(settings, store, sync, herdr, codex, processes=...)` kräver
samma settings/store som den explicit konfigurerade F25-synken. Bara den betrodda
Integration-aktören i rätt run får anropa `await park_blocked(actor, task_run_id)`
eller `await block_review(actor, task_run_id, decision)`. Worker lämnar sitt
befintliga v1-BLOCKED genom F16. Worker-rapportens format och SQLite-schema2 är
oförändrade. Routingrollen för en Worker är operatörskonfigurerad, normalt User.

En reviewfråga är `version=1`, `result="NEEDS_INPUT"`, aktuell `context_id`,
`reason`, `input_required` och `responsible_role` (User, Integration eller
Coordinator). Reviewtexten måste vara meningsfull, utan NUL, och sammanlagt högst16KiB.
Worker-blockerare bevarar hela F16:s befintliga rapportstorlek och trunkeras inte.
Rollen anger nästa ansvar och ger ingen exekveringsbehörighet. Första beslutet
kräver senaste fullständiga F18-context, exakt aktuella task/epic-SHA, riktiga
oberoende testbevis och handoff. Ett nytt kodläge kräver ny review. Sparad
historisk blockerare får speglas när andra tasks avancerar epicen; originalets
source/test ska fortfarande matcha och originaltarget ska finnas i epicens
historia. Frågan skapar ingen approval eller merge.

Operationsnyckeln för `task_attention` binds till taskrun och faktisk
källoperation. Journalen sparar oföränderligt actor/run/branch/worktree/SID/bas,
startoperation/generation/nativebinding, källa och dess hash, orsak/input/ansvar,
ursprunglig slot och blockerhändelsens hash. Oföränderligt innehåll har egen hash.
Reviewjournal och BLOCKED-händelse skrivs atomiskt. Ett separat icke blockerande
fillås per DB/taskrun hindrar överlappande Attention-anrop; övriga Git-/SQLite-
och F13-lås behåller sina faktiska ansvar. Ändrad identitet eller ursprunglig
slot avvisas före stopp.

Stadier är RECORDED, PARK_PENDING, PARKED, SYNC_PENDING och PARKED_AND_SYNCED.
Bara det sista är SUCCEEDED. F13 får en stabil stoppnyckel för journalen;
aktuellt stopp/generation, PARKED-händelse, slot=null och faktisk pane-/process-
inaktivitet kontrolleras. Ny task/run/session eller implicit input/resume skapas
inte. En äldre redan parkerad run utan F31-journal adopteras inte; dess äldre
F25-spegel och F13-bevis bevaras.

F25 använder fortfarande verkliga statehändelser. Attention-ID/hash identifierar
en separat spegelrevision när en äldre Worker-blockerare redan har publicerats;
ursprunglig historik och legacy-nycklar skrivs inte om. Nätfel lämnar outbox,
men F13 kan fortfarande parkera säkert. Återförsök efter faktiskt lyckat stopp
återanvänder stoppet och kör bara saknad spegling. Okänt stopp behåller sloten
med konkret fel; återförsök observerar samma avsikt och bekräftar faktiska fakta.

## MCP och scheduler

Operatören injicerar `task_attention=service` i `RuntimeService`; CLI öppnar ingen
implicit nätanslutning eller loop. `task_park_blocked` tar endast project_id och
task_run_id. `task_block_review` tar dessa och det strikta reviewbeslutet ovan.
Båda kräver anslutningens registrerade Integration-principal, avvisar främmande
scope/extra roll-/slot-/inaktivitetsargument och returnerar bara säkra koder och
operations-ID:n/stadium. Rå rapport/inputtext loggas eller returneras inte.

F29 tar emot både faktisk Worker-BLOCKED och explicit reviewbeslut NEEDS_INPUT.
Schedulerns redan registrerade ägare kan parkera egna verifierade blockerare
även när boarden är offline; urval, ny start och leverans kräver fortfarande
färsk board. Efter faktisk slotrelease kan en annan oberoende task väljas genom
F27, aldrig genom att kringgå beroenden. WAITING_INPUT väntar på F32; osäkert
stopp och saknad synk redovisas separat. Aktiva epics förblir Active.

## Verkligt nativeprov 2026-10-09

En ny riktig F05-run i den tidigare godkända ofarliga reporoten använde egen
Herdr-server `hc-f31-r2-20261009`, Herdr0.9.3 och Codex0.162.0. Native fixture:
epic `e8d9dbf7-4dd2-426a-87d7-8ec9dd264eaa`, task
`78dabe54-01a7-4234-a696-49774b5c4781`, korrekt User105/blitterbot-tilldelning.
Lokal run `e40c2627-87ad-4431-af28-d017de13bda3`, SID
`01a1212b-1132-7f13-aa40-e3af437b129e`. Worker läste egna regler, gav korrelerad
WORKING ACK och verklig BLOCKED för saknad RETENTION_DAYS utan gissning eller
filändring. Integration sparade blockerare
`71d62a8a-0374-40a2-83fb-d8c1fe3c6106` med input/ansvar och publicerade NeedsInput.

F13-stopp `246b0d5f-2c81-4b73-bbb5-0f7f30ac6c7a` verifierade inaktiv pane och
sex sparade processidentiteter innan slot1 frigjordes. Run blev PARKED med
resume_state WORKING och samma SID/branch/worktree/bas/commit. Native task
NeedsInput v7 återläst. Replay gav samma resultat och exakt en start, assignment,
blockerarjournal och stopp. Main samt alla äldre refs/worktrees bevarades.
[Maskinföljbara faktiska bevis](F-31-prover.json).

Första försöket är **underkänt**: provskriptet upprepade prepare_git under STARTING
och hann inte observera originalets ACK inom dess verkliga45s-gräns. Ingen
produktionstidsgräns ändrades. Sen faktisk observation sparade originalets SID
och TIMED_OUT; F13 parkerade originalrunnen med stop8d3092d5 och slot=null.
Dess native task fick NeedsInput med det konkreta provfelet; egen första server
stoppades. Alla refs/worktrees/SQLite/nativehistorik bevaras. Skriptet rättades
till att observera samma pending-intent direkt, utan ny Gitpreparation eller
assignment. Försök2 använder separat ny fixture, aldrig fabricerad recovery av
det första försöket. Det underkända försöket räknas inte som acceptans.

Provkommandon: `uv run --locked python scripts/probes/f31_native.py prepare
--attempt 2 --repository <redan-godkänd-ofarlig-reporot> --server <egen-färsk-server>`,
sedan separata native test-ID:n och `start --attempt 2`, `park --attempt 2`,
`export --attempt 2` från F31-worktreet. Återkör inte prepare mot befintlig run.
Faktiska bindningar finns i privat `.herdr/probes/f31-r2`; credentials ligger i
operatörskonfigurationen. Ny testserver och shellpane bevaras avsiktligt för
F32:s samma-session-prov, med Workern verifierat inaktiv. Fixturetasken är inte
Done: dess implementation väntar på det separata inputbeslutet. Stoppa inte dess
server innan fortsatt registrerad livscykel är avslutad eller uttryckligt
hanterad; förstaförsökets gamla server är redan stoppad. Ingen bootstrapworktree
adopterades. F38-miljögate och F47-full recovery återstår.

## Servicegrind och begränsningar

Aktuell servicegrind: `uv run --locked pytest tests/test_task_attention_service.py -x`
—20 PASS/57.38s/exit0. Riktig Git/SQLite, testprocesser och SDK-MCP med kontrollerad
board/runtime/processadapter: Worker/review, lång komplett v1-blockerare, park före
slotrelease, timeout, nätfel, legacyhistoria, stale context/HEAD, främmande roll/run,
ändrad generation/slot/journal, förlorat returvärde efter lyckat stopp och reopen.
Separat `uv run --locked pytest tests/test_task_scheduler.py tests/test_teamplayer_sync.py
tests/test_runtime_lifecycle.py tests/test_mcp.py -x` —90 PASS/450.26s/exit0 före
de två sista lokala säkerhets-/storlekskontrollerna; dessa verifieras av20-grinden
och aktuell task→epic-grind. Scheduler provar fortsatt annan task och offlineparkering.
90-regressionerna är inte ett påstående om en enda full körning på slutversionen.
Ruff/build/configCLI/diff samt aktuella54wheelmoduler/tvåpolicies/118länkar PASS.
Den första exakta wheelkontrollen hittade ett paket byggt före sista kodändringen;
paketet byggdes om och kontrollerades mot aktuell kod. Inget fel döljdes som PASS.
F31:s review/inputserviceprov är simulerade vid nativegränsen; det separata provet
ovan använder faktisk Herdr/Codex/TeamPlayer. F32 levererar input/resume, F33
samlad Attention/samtidighet. F31 öppnar ingen implicit automation eller approval.
