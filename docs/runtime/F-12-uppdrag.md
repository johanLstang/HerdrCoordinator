# F-12: korrelerat uppdrag och status

`RuntimeAssignmentService(settings, store, herdr, codex=None)` är ett internt API:

- `dispatch(actor, task_run_id, instruction, timeout_seconds=45)` sänder högst en prompt.
- `observe(actor, task_run_id)` återläser bekräftelse och fysisk status utan sändning.

Integration-principalen måste ha rätt projekt/epic. Tasken ska vara STARTING, äga sitt F-05-worktree/branch, ha reserverad slot och en lyckad F-11-startoperation. Server, sandbox, pane/terminal, agentnamn och faktiska processidentiteter jämförs med startjournalen. Upprepning av en bekräftad operation är tillåten medan run/epic fortfarande är aktiva och resurserna kan verifieras.

## Transport och bekräftelse

En JSON-envelope `HERDR_ASSIGNMENT` version 1 innehåller instruktionen, exakt project/epic/run/task och ny UUID-korrelation. Worker ombeds svara med exakt assignment-objekt:

```json
{"status":"WORKING","project_id":"p","epic_run_id":"e","task_run_id":"t","task_id":"F","correlation_id":"uuid"}
```

Operation `dispatch_assignment` är unik per project/kind/run. Före extern sändning sparas intent, startoperation, korrelation, SHA256 av instruktion och prompt, deadline och baslinjens Codex-session/turn-ID. Instruktion/råsvar lagras inte i operationsloggen. Timeout är ett heltal 1–45 sekunder och instruktionen högst 32768 UTF-8-bytes. Ändrad instruktion eller timeout på samma run avvisas; ny tilldelnings-/fixcykel ligger utanför F-12.

Herdr `agent prompt <registrerat namn> TEXT --wait --timeout MS` utför transport. RETURNED betyder att transporten återkom; UNKNOWN behåller journalen och observeras. Inget av dessa är en domänbekräftelse. Ingen blind omsändning görs, även vid krasch mellan intent och extern sändning.

Native Herdr-session-ID binds till verklig Codex `thread/read` med exakt id/sessionId/cwd. `includeTurns:true` ger typade meddelanden. Endast en ny, ej baslinjeregistrerad turn i completed/inProgress kan bekräfta start: samma turn ska innehålla den exakta skickade userMessage-promptens hash och ett agentMessage vars JSON matchar samtliga ACK-fält. Promptens egen JSON, terminaltext, gamla turns, annan session/run/korrelation, failed/interrupted-turn eller allmän READY_FOR_REVIEW accepteras inte.

Session-ID sparas när native identitet och thread verifierats, även om ACK saknas. Ett giltigt ACK skriver atomiskt sessionmetadata, StateService STARTING → WORKING och unik `assignment-ack:<operation-id>` samt turn/item/session/message-hash i journalen. Dubblett/reopen returnerar samma event och skapar inget nytt. Detta bekräftar start; inga commit-/review-/Done-bevis produceras.

## Timeout och status

Uteblivet ACK ger WAITING med observerad fysisk Herdr-status. Anroparen kan fortsätta `observe` till deadline; tjänsten driver ingen bakgrundsloop. Vid passerad deadline fryses operationen TIMED_OUT och ger ASSIGNMENT_ACK_TIMEOUT. Även ett senare matchande svar får inte då sätta WORKING. Tasken behåller STARTING och sin slot. Okänd eller blockerad runtime ger konkret fel utan att räknas som inaktiv; operatörsbeslut hanteras utanför denna service.

CONFIRMED innehåller historiskt bekräftelseevent och separat aktuell `runtime_status`. idle/done betyder inputberedskap och innebär ingen READY_FOR_REVIEW eller task-Done. Session, branch/worktree och reserverad slot bevaras; park/stopp/resume/slotrelease levereras i F-13. MCP-serverns publika verktyg är fortfarande read-only.

## Verifiering

Kontrollerade transporter använder verkliga temporära Git-worktrees och SQLite. Förlorat transportsvar, avbrott före sändning, försenat/uteblivet ACK, gamla/främmande/user-echo-signaler, fel resurser/roller och konkurrerande dispatch verifieras. Verkligt prov använder det operatörsgodkända ofarliga Git-testrepot, registrerade test-runs och isolerad server `hc-f12-20261008`; ingen kodimplementation eller delegation ingår i prompten. Se [sanerade prov](F-12-prover.json).
