# F23: verifierat TeamPlayer MCP-kontrakt

Verifierat 2026-10-08 genom riktig Streamable HTTP MCP, låst `mcp==2.3.0`,
protokoll `2025-11-25`. [Serverkatalogen](F-23-katalog.json) innehåller exakta
inputscheman och read/write-annotationer. [Proven](F-23-prover.json) innehåller
utvalda fixturedata, inga headers, tokens eller privata responser.

## Identitet och åtkomst

Projekt HerdrCoordinator `d2ee4c75-7b80-465f-83ac-1750854a8e80`, Write.
`get_me` ger blitterbot@gmail.com, User `105f26a7-0648-438d-94fd-3260ac3af4ee`.
Testepic `8051e9de-f4dc-4277-8f80-62b1c36c4690` har ett uttryckligt `[TEST]`-namn.
Tasks `f29251bf-a8c0-4c42-b3a4-1d5a426193db` och
`c650f6d0-ffff-4ca0-a772-16c59af8baf2` är båda User-tilldelade till detta konto;
den andra beror på den första. Inga andra projekt skrivs.

Kontots projektgrant är API-behörighet, inte produktens Integration-/Worker-roll.
Produktservicen måste kontrollera betrodd Actor/run separat i F25. Ett Read-konto
har inte provats; dagens Write-konto har verkligt verifierade läs- och skrivprov.

## Läsning och fält

| Logisk operation | Verkligt verktyg och svar |
| --- | --- |
| Projekt/identitet | `list_projects({})` → array; `get_me({})` → userId/email |
| Epics | `list_epics({projectId})` → array med `id`, projectId, name, description, status, version |
| Epic | Filtrera ovan på exakt board-UUID; inget separat get_epic-verktyg |
| Tasks | `list_tasks({projectId})` → objekt med `tasks`, projectId, status, responsibleAgentId |
| Task/beroenden | `get_task({projectId,taskId})` → objekt; dependencyTaskIds är array av task-UUID:n |
| Kandidater | Hela listan, eller `search_tasks` för sökning; söklimit bevisar inte komplett backlogg |

Taskfält: taskId/projectId/epicId, name/description, status/taskType/priority,
acceptanceCriteria/traceabilityArtifactIds/dependencyTaskIds, version,
executionOwnerKind/responsibleUserId/responsibleAgentId. Bevara text, ordning,
radbrytningar och Unicode utan normalisering. Native High returnerade talet `2`.
Lokal TeamPlayer-källa TaskPriority anger Low=0, Medium=1, High=2, Critical=3;
F24 ska avvisa okända värden och bevara det externa värdet, inte anta P0–P2.

Board-epics är inte körbara tasks. `list_tasks` kan innehålla äldre taskType=Epic;
dessa får inte startas som vanliga tasks eller ersätta list_epics. Aktuellt projekt
har 50 implementationstasks och två separata fixturetasks. Beroenden är task-ID:n,
inte lokala F-ID:n. Lokal backlogg styr scope/ordning och föregående epicens
verifierade main-leverans; en extern Done-sträng bevisar inte Git-integration.

`tools/list` har cursor/next_cursor; harnessen går till null och skyddar mot loop.
Dagens `list_tasks` och `list_epics` saknar cursor/page/limit i input och svar.
Använd full datalista och deduplicera/validera UUID:n; uppfinn ingen pagination.
Två efterföljande fulla läsningar och list/get av fixturetasks var identiska.
Katalogändring kräver nytt kontrakt; inga framtida sidor får ignoreras tyst.

## Skrivning, versioner och historik

| Operation | Verifierat kontrakt/behörighet |
| --- | --- |
| create_epic | projectId/name/description/idempotencyKey; initial fixture skapad och återläst |
| create_task | request plus idempotencyKey; två verkliga skapanden och identiska återförsök |
| update_task_status | request.projectId/taskId/version/status/statusReason; färsk version och readback |
| update_task_details | request.projectId/taskId/version/changeReason/description m.fl.; historik bevarad och readback |
| update_epic_status | projectId/epicId/version/status; verkligt fixture-InProgress och stale-rejection |
| update_epic_details | projectId/epicId/version/description; riktig fixturehändelse exakt en gång |
| set_task_epic/assign_task_to_me | Exakta scheman finns; inte behövda för F24/F25 och inte skrivprovade här |

Statusprov på första fixturetask: Pending → InProgress → NeedsInput → InProgress
→ Pending. Detta är deklarerade transporttestdata, inga produktruns/start/merge
eller falsk Done. Testepicen behålls InProgress efter aktivt kontraktsprov.
Done-skrivbehörighet med detta konto är dessutom faktisk i F21/F22/E05 efter
verifierad Git-leverans, dokumenterad i [E05-review](../reviews/E-05.md).
Testing/Paused/Blocked/NeedsReview m.fl. följer projektets [statusmappning](../../AGENTS.md).
Epics begränsas av produkten till Pending/InProgress/Done; native schema har fri
sträng och bevisar ingen produktpolicy. Återöppning kräver dokumenterat beslut.

Stale task-/epicversion ger toppnivå `success:false, code:version_conflict,
currentVersion`. Saknad task ger `success:false, error:{errorType:not_found,
errorCode:task_not_found,...}`. Fel/otillgängligt projekt gav toppnivå
`code:permission_denied`. Adaptern måste hantera båda felsvaren och MCP isError,
utan att logga rå message/arguments. Alla negativa prov lämnade fakta oförändrade.

### Fristående kommentarer och reopen

Serverns samtliga 16 verktyg saknar comment/read_comments, delete, reopen och
beroenderedigering. Detta är konkreta kapabilitetsbegränsningar, inte antagna API:n.
F39 kan använda verifierade create_epic/create_task; inget separat reopen finns.
Done → InProgress genom statusverktyget är inte provat på fixture och får inte
antas vara en godkänd produktåteröppning utan besluts- och leveranskontroller.

Projektets redan dokumenterade fallback är historik i taskbeskrivningen.
F25:s logiska `add_task_comment` använder därför **beskrivningshändelser**, inte
fristående TeamPlayer-kommentarer: hämta färsk task, bevara befintlig beskrivning,
lägg till markerad händelse med stabilt ID och innehållsdigest, skriv med CAS-version
och återläs. Samma event/innehåll innebär ingen ny skrivning; avvikande innehåll,
manuella ändringar eller okänt utfall kräver avstämning. Event saknat efter okänt
utfall ger ingen blind omsändning med ny version. Beständig synkavsikt ska skilja
status och textdelsteg från lokal Git-/runtime-framgång. F23 provade en markerad
händelse och omkörning gav exakt en kopia, men F25:s beständiga produktservice
och felinjektion återstår. Native fristående kommentarkapabilitet förblir false.

## Operatörskonfiguration och prov

Endpoint och credentials kommer från lokal operatörskonfiguration, inte repository
eller agentargument. Pi använder loopback `/mcp` och lokal `http_headers_helper`
som hämtar/förnyar Authorization i minnet. Första direktprovet saknade denna helper
(get_me via etablerad connector fungerade samtidigt) och initialize gav -32603.
Efter att helpern användes förhandlades 2025-11-25 och alla avgränsade prov passerade;
det finns inget kvarstående SDK-/nätblockerande fel. HTTPS eller loopback utan URL-
credentials krävs. Bearer env-var/env-headers stöds också; värden får aldrig sparas
i proof/logg/prompt. Helper är betrodd operatörskod och körs utan shell, med timeout.
HTTP/SDK-loggning stängs av i harnessen och externa fel skrivs som fast säker text.

Från task-worktree:

```bash
uv run --locked python scripts/probes/f23_mcp.py catalog
# Integration-testoperatör, endast fastlåst [TEST]-epic:
uv run --locked python scripts/probes/f23_mcp.py fixture
uv run --locked python scripts/probes/f23_mcp.py verify
```

Alla tre samt fixture-omkörning passerade exit0. Skrivprov utfördes sekventiellt av
Integration-testoperatören, inte Worker. Rå respons och journal finns i ignorerad
`.herdr/probes/f23`; kopiera endast uttryckligt utvalda proof/schemafält.
Statusavsikt sparas före skrivning och okänt svar återläses före omsändning.
Ingen Herdr/Codex-session startas. Harnessen är ett operatörsprov; produktens
TeamPlayer-transport/reader och beständig statussynk byggs först i F24/F25.
