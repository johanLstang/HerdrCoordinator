# F24: validerad TeamPlayer-läsning

`TeamPlayerReader` läser hela det konfigurerade projektet via fem verifierade
read-only verktyg. `teamplayer_connection(endpoint, OperatorHeaders(...))` öppnas
explicit av operatören; get_me, projektgrant och valfritt projektnamn valideras.
Ingen ny MCP-tool, CLI-loop, runtime-start, statusändring eller SQLite-migration
introduceras. F25 bygger skrivning och beständiga synkavsikter separat.

## Data och tillitsgräns

`BoardEpic`, `BoardTask` och `BoardSnapshot` är frysta domänobjekt för externa
fakta. Runtime EpicRun/TaskRun och Git-/reviewbevis skrivs inte genom läsningen.
Taskprioritet Low/Medium/High/Critical bevaras som 0/1/2/3; text, Unicode,
acceptansordning, dependencyTaskIds och traceabilityArtifactIds bevaras exakt.
Källreferenser i description bevaras som text; ingen Markdown används som kod
eller som ny behörighet. Operatören tillför explicit `LocalBoardBinding(local_id,
sources)` per externt UUID. Detta bevarar lokala E-/F-ID:n och källpaths utan att
gissa från rubriker eller fabricera en LocalTaskSpec. Befintliga beständiga
ExternalReference/runbindningar kan användas för att tillföra dessa kopplingar;
läsaren skapar inga runtime-records bara för att ett boardobjekt finns.

Board-epics kommer från list_epics, med egen status/version. Legacy taskType=Epic
är uttryckligen ej körbar. Null/missing epic, saknad instruktion/acceptans,
fel executionOwnerKind/responsibleUserId, oavslutad task i Done-epic, saknat/ogiltigt beroende, cykel och
Cancelled flaggar berört arbete. `responsibleAgentId` bevaras som historiskt fält
men ger ingen User-tilldelning. Cancelled kan mappas till boardens Done-kolumn;
det är aldrig verifierad Done och uppfyller inte ett beroende. Cykler och fel
sprids till beroende tasks även om en av de felaktiga noderna manuellt fått Done.

`snapshot.candidate(task_id)` betyder endast möjlig boardkandidat: Pending,
körbar tasktyp, inga graf-/ägar-/innehållsfel. Funktionen ger inte startbehörighet
eller VerifiedFacts. F27 och befintlig TaskStartService måste dessutom kontrollera
lokal taskspecifikation, ordning, tidigare epicens main-leverans, faktisk granskad
beroendeintegration, runtime-ägare och kapacitet. Läsaren är internt operatörs-API,
inte ett Worker-verktyg som exponerar hela projektet till en agent.

## Komplett läsning och förändringar

MCP-katalogen hämtas sida för sida till null next_cursor, med skydd mot loop,
dubbla tools, saknade/read→write-förändrade kapabiliteter och okänt schema.
Dagens data-API har ingen pagination; epics är full array och list_tasks har fullt
tasks-objekt. Läsaren gör två kompletta läsningar, sorterar på UUID och kräver lika
validerade innehåll/versioner. Högst tre försök; fortsatt förändring ger ett säkert
SNAPSHOT_CHANGED, ingen partiell snapshot eller automatisk start.

Dubbla UUID:n, epic/task-ID-kollision, fel projectId, oväntad filterecho eller
listmetadata avvisas. Ny cursor/page-kapabilitet eller fortsättningsmarkör ger
PAGINATION_UNSUPPORTED tills kontraktet är verifierat/implementerat. Detta följer
[F23:s verkliga kontrakt](F-23-kontrakt.md); inga okända sidor ignoreras tyst.
Två lika läsningar är ingen servertransaktion eller låsning; nästa beslut måste
återvalidera aktuell task/version och beroenden.

Borttagna tasks upptäcks genom saknade beroenden, explicit binding, `previous`
snapshot och ID:n som försvann mellan delavläsningar. get_task kontrollerar
identitet/projekt före läsning, avvisar saknad/felaktig task och bevarar aktuell
version. Ett enskilt get_task ger fakta, inte en validerad full graf.

## Credentials och transportfel

Endpoint måste vara HTTPS eller loopback, utan userinfo/query/fragment. Credentials
levereras i minnet av betrodd operatörsprovider, som väljer env-var eller bounded
argv för befintlig lokalt installerad autentiseringshelper. Inget shell, inga
credentialvärden i domänobjekt, provider-repr, loggar, proof eller konfigurationsdump.
Raw HTTP/MCP/JSON/helperfel ersätts med fasta TeamPlayerError-koder utan serverns
message/body/headers. MCP isError och båda verifierade fel-envelopes hanteras.
Dubbla JSONnycklar och svar över16MiB avvisas. Anslutningen förhandlar legacy
protokoll med låst SDK. httpx2 deklareras nu som direkt beroende; uv.lock ändrar
endast projektets beroendemetadata, inga låsta tredjepartsversioner.

## Verifiering

```bash
uv run --locked pytest tests/test_teamplayer_reader.py tests/test_states.py tests/test_mcp.py tests/test_startup.py -q
uv run --locked python scripts/probes/f24_read.py
uv run --locked ruff check .
uv build
```

113 tester passerade14.40s: lossless/bindings, read-only nätoperationer, grafcykel,
felaktig Done-nod/descendants, saknad/borttagen task, version-/innehållsförändring,
dubbla UUID:n, okänd pagination/filter, konto/projekt, native envelopes/isError,
credentialprovider/säker endpoint och riktig in-process MCP-transport med två
katalogsidor. Testad transport mot denna fixture är simulerad TeamPlayer; det
separata [verkliga produktadapterprovet](F-24-prover.json) läste HerdrCoordinator
13epics/52tasks och två externa fixturetasks, identiska upprepningar och list/get,
korrekt DEPENDENCY_NOT_DONE och bevarad explicit source/local-ID-bindning.
Native prov/Ruff/build/diff passerade exit0; inga andra epics/tasks ändrades.

Initiala unitkörningar hade 51pass/1fail, sedan111pass/1fail eftersom testserverns
callbacks inte registrerades i Server-konstruktorn (Method not found). Fixture
rättades till SDK:s verkliga API; produktens kontroller och assertions försvagades
inte. Slutresultatet113pass ovan avser rättad fixture och aktuellt produktunderlag.
Integrationreview/Task→Epic-merge och verifiering på mergecommit återstår.
