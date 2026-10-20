# F41: begränsad körmiljö och eget commitverktyg

Status 2026-10-20: implementation/provförberedelse, **ingen nativeacceptans ännu**.
F41.A1–A3 står kvar som ej verifierade. Se [förkontraktet](F-41-runtime-plan.md).

## Valfritt sessionskontrakt

`CodexExecutionPolicy` är en intern operatörsmodell, inte ett MCP-argument eller
en automatisk standard. `HerdrAdapter(..., execution_policy=policy, mcp=launch)`
använder Linuxprofilen i just den sessionen. `workspace_access` måste motsvara
registrerad sandboxroll: Worker write, Coordinator/Integration read. Legacy utan
profil behåller tidigare argv och fingerprint.

Profilen nekar rotfilsystemet, tillåter minimal runtime och egen arbetsyta, skyddar
egen `.git/.codex/.agents/.aws` och nekar nätverk. Shellmiljön är `inherit=none`
med explicit PATH. En separat read-only hjälparkatalog innehåller endast
`codex-linux-sandbox` till en explicit SHA256-bunden faktisk binär. Inga globala
Codexinställningar ändras. Start och observation verifierar samma fullständiga
argv; extra flaggor, annan faktisk nativebinär, ändrad hjälpfil eller policy ger
fel. Node-launcher ensam räcker inte som profilbevis.

`CodexMCPLaunch.auto_approved_tools` är en ändlig lista tillåtna verktygsnamn för
servern `herdr_coordinator`. Den binds till fingerprint. Tom lista är oförändrad
standard. Detta är operatörskonfiguration som kräver det konkreta provets beslut;
modellen ger inga rättigheter till andra servrar eller roller.

Native preflight visade att `auto` inte innebär ett förhandsgodkännande av
skrivverktyg: Codex nekade commit före serviceeffekt med approval policy never.
Den uttryckligt användargodkända ändliga listan kodas därför med per-tool
`approval_mode="approve"`, enligt [Codex MCP-konfiguration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
Första Worker/SID/dirty worktree och dess BLOCKED-rapport är bevarade och fysiskt
stoppade med släppt slot, utan commit eller positiv grind. Ett nytt separat
preflightprov verifierar den korrigerade registreringen; inget gammalt
startfingerprint eller pågående runtime skrivs om för att få kontroller att passa.

## Worker-commitservice

Den separata `RuntimeService(..., worker_commit=service)` publicerar exakt:

| Verktyg | Argument | Avgränsning |
| --- | --- | --- |
| `worker_workspace` | inga | Faktisk egen branch, HEAD och arbetsstatus. |
| `worker_commit` | `request_key`, `message` | Commit av egen registrerad task. |

Inga fria paths, refs, SHA, kommandon eller rollargument accepteras. Samma
RuntimeService får inte samtidigt innehålla andra muterande tjänster. Den betrodda
anslutningen måste vara Worker för verklig TaskRun. Servicekontroller kräver
aktiv originalepic, F05-ägarskap, unik slot, task_start/ACK, aktuell start-
registrering, profil-/MCP-hash, pane/cwd, nativeprocesser och samma verkliga SID.
Stdio-klientens faktiska föräldra-PID måste finnas bland verifierade Codexrötter.
Pågående lifecycleoperation blockerar commit. Ny/pågående commit kräver WORKING;
ett gammalt lyckat kvitto ger ingen ny mutationsrätt.

Operationstypen är `worker_commit`; nyckeln är SHA256 av `[task_run_id, request_key]`.
Ingen schemamigration behövs. Befintlig Operation sparar avgränsad fil-/innehålls-
hash, parent, actor, SID, startoperation, konfiguration, fryst author/tid och
senare tree/commit. Den lagrar inte filernas råa bytes eller credentials.

| Sparad fas | Nästa effekt / återhämtning |
| --- | --- |
| INTENT | Bygg träd i privat index utanför Worker-worktree. |
| TREE_READY | Deterministisk commit-tree med samma author/tid/parent/tree/marker. |
| REF_PENDING | CAS på enbart `refs/heads/<egen taskbranch>`; återläs känt utfall. |
| INDEX_PENDING | Synka index utan `-u`; skriv aldrig över arbetsfiler. |
| COMMITTED | Läs faktiskt resultat; inget nytt Git-/databasresultat vid samma replay. |

Gemensamt Git-lås serialiserar operationen. Filöppning använder openat och
O_NOFOLLOW för varje komponent; bara vanliga filer, högst256 filer/8MiB totalt.
Staged input, rename, osäkra indexflaggor, metadata/symlänkar och drift nekas.
Git kör utan hooks/signering/replace och innehåll skrivs med `--no-filters`.
Dessutom nekas konfigurerade filter **före även git status**, eftersom status kan
köra clean/process-filter. Commitmeddelande kan inte injicera operationmarker.

Efter okänt svar: använd ursprunglig nyckel. En annan nyckel kan inte ersätta en
pågående intention. Ändrade input/HEAD/index/identiteter ger fel och bevarar arbetet;
inget reset, cleanup eller blind omcommit. Kvitto är inte review, merge eller Done.

## Ursprunglig förberedelse: separat F41-r1

Förberett under `/tmp/herdr-f41-r1-20261020`, eget repo och skyddade0600-filer.
Named server: `hc-f41-r1-20261020`. Vid denna ursprungliga förberedelse hade
server/agentsessioner ännu inte startats. Senare faktiska försök och stopp finns i
[aktuellt underlag](F-41-native-progress.json).
Setup gör ingen scheduling, approval eller merge. Nativehosten återbygger scope
från faktiska F38-claims och registrerade native cwd/PID; utvecklingsworktrees
adopteras inte. MCP-handshake kan ske före ACK, men varje anrop återvalideras.

| TeamPlayer-prov | Faktisk UUID |
| --- | --- |
| Primär provepic | bff4790c-45f9-41db-8019-9cade6e5485a |
| A Unicode labels | 5b4b2e28-91eb-4aac-a8fa-ecd2526be4d1 |
| B even totals | b46b9ec2-11c2-4f72-8649-b91f34f4cc68 |
| C caption, beror på A/B | 44c1f43f-2b81-49a6-a18d-26a53ea2ce91 |
| Nästa provepic, beror på primär mainleverans | bb7592a9-f193-4df7-9133-6254fce85b53 |
| D report på ny main | c6bf629a-fe6f-452d-9a4f-d91bc84143ed |

Alla fyra tasks återlästa som Pending/User105. Vid skapande av A/B lästes först
fel ID-fältnamn i observationskoden; list_tasks återlästes, riktiga taskId sparades
och C/D skapades med dessa. Inga dubbla eller ersättande tasks skapades.

Provets två avsiktliga täckningsbrister är uttryckliga i uppdraget. A:s första
strip/validation-utkast lämnar NFC öppet med ärlig begränsning; native Integration
ska hitta verklig kodbrist och begära korrigering i samma Worker. Primär epicens
publika api.py täcks inte av baseline-handoffens tester; Coordinators oberoende
aktuella verifiering ska hitta bristen och skapa kopplad templatekorrigering.
Inga fabricerade fel, testresultat eller godkännanden accepteras. Alla ursprungliga
taskbevis bevaras; finalreview måste bedöma uppdaterad kod och medlemskap.

## Mätning och körordning

`native_f41_gate.measure` utför ofarliga riktiga kernelprov för alla tre profiler
och exakt Bubblewrap-testargv. Target mount är read-only, scratch privat, env tom,
PID-/mount-/nätvy separerad. Inga hostcredentials eller state monteras. Testprocess
kan skapa egen lokal socket i sin privata nätvy; grinden mäter att en faktisk
värd-TCP-listener inte kan nås, i stället för att kalla lokal bindning ett escape.

Före första autonoma claim krävs en **verklig** separat registrerad preflight-
Worker som gör egen brokercommit och sedan fysiskt stoppas. Grinden återläser
verkligt TaskRun/ACK/SID, native MCP-call, commitmarker/parent och F13-stopp/slot.
Den mäter OS-gränser på nytt och binds till fryst källkod, alla profiler,
verktygskataloger, testargv, rootkonfiguration och faktisk main. Saknat nativebevis
eller receipt ger WAIT. Den fristående mätningen är inte en positiv nativegrind.
Vid ny main mäts samma registrerade gräns på nytt före automatisk fortsättning.

Efter separat uttryckligt F41-r1-beslut sparar operatören en skyddad kvittens för
exakt scope och fryst komposition. Den kan inte skapas genom agentverktyg.

1. Starta registrerad preflight med `native_f41_probe.py <preflight.json> start-preflight`.
2. Läs faktisk session/commit, stoppa samma Worker och verifiera fysisk inaktivitet.
3. Starta Coordinator, verifiera dess ACK och skapa verkligt aktuell gate-receipt.
4. Ge en journalförd begäran att genomföra hela registrerade provplanen. Native
   Coordinator/Integration fattar och utför alla scheduling/review/mergebeslut.
5. Läs tidslinje, roller, slotgräns, reviews, alla SHA/tester/Done/fortsättning.
6. Stoppa bara egna kvarvarande runtimes; bevara refs, worktrees och SQLite.

Operatörens kommandon kräver `HERDR_ENV=1` och README:s `uv run --locked python`.
Privata JSON-värden/headers ska inte kopieras till Git eller rapportutskrifter.

## Kvarvarande leveransgrind

Det konkreta nativeprovet, faktisk tidslinje, full featureacceptans, aktuell
Task→E10-review/merge/eftertest och E10:s samlade mainintegration återstår.
Förberedelse eller passerade enhetstester ger inte F41 Done eller E10 Done.


## Nativeupptäckta återförsökskontrakt

`integration_overview` lämnar exakt `task_spec` endast för den egna registrerade
CLAIMED/STARTING-rad som erbjuder task_start. Källa är färsk native boardbindning
och samma schedulers validerade konfiguration. Saknad, främmande eller tvetydig
bindning ger inget ersättande underlag. Kopiera det exakta värdet; grundkraven i
starttjänsten, ägarkontrollen och beroendeverifieringen består.

Nya `dispatch_assignment` börjar i PREPARED. Förvillkor och ursprunglig deadline
kontrolleras igen före atomisk DISPATCH_REQUESTED, därefter exakt ett transport-
försök. Förlorat svar eller avbrott efter markeringen återhämtas med observation,
aldrig blind send. Äldre DISPATCH_REQUESTED utan transportfält är också okända;
de skrivs inte om till PREPARED. PREPARED kan inte godkännas som native ACK.
Ingen schemaändring, deadlineförlängning eller efterhandsadoption sker.

R13 visade verklig automatisk Integration-aktivering, två samtidigt arbetande
Workers, tre granskade/integrerade/stoppade tasks och Coordinatorns fullständiga
sexdelade review med verkligt API-fel. Korrigeringen återfördes till samma
Integration, men saknad läsbar exakt spec krävde diagnostisk datahjälp och original-
assignmentens ACK förblev obekräftad. Samtliga egna runtimes är fysiskt stoppade,
slots null och91 frysta källfiler bevarade. R13 räknas inte som full autonomi.
En ny komplett fryst körning krävs efter de beskrivna rättningarna.
