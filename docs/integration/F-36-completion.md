# F36 — samlad epicverifiering och överlämning

## Operatörskonfiguration

`EpicCompletionService(epic_start, scheduler, spec)` använder samma registrerade
F34-principal och F35-scheduler. `EpicCompletionSpec` har kontraktsversion1,
separat positiv `acceptance_version`, explicit icke-tomt taskscope, krav,
acceptanskriterier, versionshanterade källor och kända begränsningar. Scope,
krav, kriterier och källor måste motsvara den registrerade F34-specifikationen.
Varje `CompletionCommand` har ett explicit argv och timeout1–3600sekunder;
operatören anger separata kommandon för `build`, `test` och `acceptance`.
Acceptanskommandot måste faktiskt kontrollera epicens samlade kriterier.
Använd inga credentials i argv, spec, rapport eller versionshanterade filer.
Processoutput returneras eller loggas inte av denna tjänst.

Konfigurationen kopieras till kanonisk JSON, versionsbinds och hashas.
Nästan oföränderliga modellobjekt med muterbara listor används inte som
beständig konfiguration. Agenten får inte byta scope, kriterier, kommando,
roll, miljö eller timeout via MCP.

Komponera `IntegrationControlService(epic_start, scheduler, log,
epic_completion=completion)`. Det valfria tillägget annonserar
`epic_complete(project_id, epic_run_id, request_key)` i det verkliga MCP-SDK.
Utan tillägget finns verktyget inte. F34:s verkliga registrering kontrolleras
vid varje anrop och vid omverifiering. Worker och främmande anslutning saknar
behörighet. Operationen utför inga taskstarter, godkännanden eller main-merger.

## Grindar och underlag

Schedulerlåset tas före Git-integrationslåset. Samtliga ingående tasks måste
finnas i både det explicit konfigurerade scopet, SQLite och färsk TeamPlayer.
Även en ny task utan lokal bindning stoppar verifieringen. Alla måste vara
verklig Done, tilldelade verifierad User, utan reservation, med godkänd aktuell
review, komplett reviewcontext, verifierad faktisk Git-merge, oberoende
exit0-eftertest och fysiskt bekräftad stoppad Worker. Cancelled räknas inte som
Done. Krav, kriterier och beroenden måste matcha taskens registrerade spec.

Epic/main måste vara rena, utan osäkra indexflaggor eller pågående Git-operation;
epicen måste innehålla aktuell main. Underlaget innehåller båda SHA, exakt
medlemskap, faktisk taskrapport, full reviewcontext/approval, source/merge-SHA,
test-/stopp-/run-/runtimeidentiteter, taskspecifikationernas hash,
acceptansrevision och konfigurationshash. Diff mot main sparas komplett som
base64 med byteantal, SHA256 och reproducerbart argv. Ofullständig diff eller
rapport över16MiB nekas. Kända begränsningar och taskernas rapporterade
begränsningar bevaras; agentrapporten ersätter inga verifierade fakta.

## Persistens, avbrott och återförsök

Befintligt schema2 och generella `Operation` används, utan migration.
`epic_complete` identifieras unikt av project/kind/request_key och måste höra
till exakt epicrun, utan taskrun. Varje process har egen
`epic_completion_step`, nyckel `<parent_operation_uuid>:build|test|acceptance`.
Avsikten sparas före processstart, faktiskt exit-/felresultat efter processen
före nästa observation. En ny läsning av Git, board, medlemskap, runtime och
konfiguration krävs mellan stegen och före överlämning.

En känd lyckad process upprepas inte vid återförsök på samma parentnyckel.
En kvarvarande PENDING-process har okänt utfall och får inte automatiskt köras
igen på samma nyckel: avstäm resurser och använd ett nytt explicit
verifieringsanrop. En känd processförlust/timeout eller exitfel sparas som
FAILED med precist build/test/acceptance-fel och nästa åtgärd. Samma nyckel
returnerar det kända felet; efter rättning krävs ny nyckel. Ingen känd merge
upprepas för att reparera verifiering eller board.

Bara samtliga tre faktiska exit0 och oförändrade grindar ger atomiskt sparad
`EPIC_READY_FOR_REVIEW` och intern `READY_FOR_REVIEW`. Boardepic förblir Active;
Coordinator tar slutreview/PR/main-integration separat. Ett nytt
verifieringsanrop ersätter äldre underlag och återför intern READY till ACTIVE
med dokumenterat skäl. Misslyckad ny verifiering lämnar ACTIVE.
`require_current` omprövar det senaste underlaget mot faktiska resurser;
ändrade commits, tasklista, status, owner, acceptans eller konfiguration gör
äldre rapport ogiltig. Vid nya krav/taskmedlemskap behövs korrekt uppdaterad
operatörsregistrering; tjänsten adopterar inte ett främmande eller ändrat run.

## Verifieringsgräns

F36-proven använder verkliga temporära Git-repositories/worktrees,
SQLite, processer och MCP-SDK med kontrollerade runtime-/TeamPlayer-adaptrar.
F37 verifierar det samlade nativeflödet och E09:s egen epicacceptans.
Tidigare F35:s r5-prov är separat verkligt underlag, med dess dokumenterade
operatoråterställningar; det ersätter inte F37-provet.
