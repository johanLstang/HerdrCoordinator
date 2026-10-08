# D-04: Worker-uppdrag och rapport, version 1

F-14 levererar validerad lokal JSON-inmatning, en reproducerbar prompt och en ren rapportparser. Den startar ingen runtime, ändrar inget state och utfärdar ingen behörighet. F-15 kopplar uppdraget till start; F-16 verifierar rapportens Git- och testpåståenden. MCP är fortsatt read-only.

## Lokal task och prompt

[Taskschema](local-task-v1.schema.json) och [exempel](local-task.example.json) anger mål, krav, scope, avgränsningar, acceptans, källor och verifieringssteg. Projekt/epic måste matcha registrerad EpicRun; task-ID måste matcha TaskRun. Saknade obligatoriska fält, tom acceptans, främmande epic, extra eller dubbla JSON-fält avvisas. Inmatningen begränsas till 64 KiB och felmeddelanden återger inte taskinnehållet.

`load_local_task(path, epic)` validerar filen. `build_assignment(spec, task, epic)` binder registrerade run-ID:n, branch, absolut worktree, epicbranch och full bas-SHA. `render_worker_prompt(assignment)` kombinerar versionerad [Worker-policy](../../prompts/worker-v1.md), kanonisk JSON och [rapportschema](report-v1.schema.json). Samma indata ger samma text; metadata körs inte som kommandon. [Uppdragsschema](assignment-v1.schema.json) är separat från lokal taskspec. Policyn ingår även i wheel/sdist.

## Slutrapport och proveniens

Version 1 har status `READY_FOR_REVIEW` eller `BLOCKED` med exakt projekt/epic/task/run/branch. READY kräver full commit-SHA, sammanfattning och testbeskrivning; nya Workers rapporterar även verkliga kommando-argv, exitkoder, ändrade relativa filer och begränsningar. BLOCKED kräver konkret orsak och behövd input/åtgärd. Ingen rapport innebär godkänd review, merge eller Done.

`parse_worker_report(text, task=..., epic=..., source_session_id=...)` kräver att betrodd runtimeadapter levererar avsändarsessionen och att den matchar den registrerade taskens Codex-session. Worker-/verktygsargument får inte användas som proveniens. Detta interna API exponeras inte som ett godtyckligt MCP-skrivverktyg. E-03:s korrelerade WORKING-ACK är ett separat transportmeddelande, inte slutrapporten.

## Äldre textformat

Källexemplens `STATUS:`, `TASK:` och `TASK_ID:` normaliseras endast med verifierad sessions-/runbindning. Alla angivna identitetsfält måste matcha; READY kräver uttryckligt task-ID och branch. Ett äldre BLOCKED-exempel utan ID kan endast bindas genom betrodd sessionsproveniens. TASK och TASK_ID tillsammans, dubbla fält, okända fält/statusar/versioner eller främmande identitet avvisas.

En kort äldre commit expanderas endast när den matchar taskens fulla **oberoende observerade** current_commit. Utan sådant underlag avvisas den. `TESTS: PASS` behålls som ett påstående i test_summary och skapar inga påhittade testkommandon eller exitkoder. F-16 måste kontrollera underlaget innan READY registreras. JSON-formatet kräver full SHA direkt.

## Verifiering

`uv run --locked pytest tests/test_worker_contracts.py`: 35 passerade. Kontrollerar reproducerbar prompt, scope/källor, saknad acceptans, främmande epic/run/session, okända versioner, dubbla fält, falsk identitet, osäkra filpaths och båda källexemplens textvarianter. Wheel-resursen kontrolleras separat. Ingen simulerad parserkörning redovisas som verkligt Worker-/Git-/testbevis.
