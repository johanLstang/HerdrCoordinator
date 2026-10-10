# F34: registrerad epicstart

## Operatörens startkedja

`EpicIntegrationSpec` version 1 innehåller epicmål, krav, acceptans, relativa
versionerade källor, projektinstruktioner, externa villkor och ordnad tasklista
med fulla `LocalTaskSpec`, prioriteter och beroenden. Start utför inga tasks.

Operatören läser Coordinator/Integration-profiler med F04 `load_principal`, från
owner-only filer i skyddad katalog utanför repositories och alla worktrees.
Integration binds till ett bestämt epicrun. Toolargument ersätter inte profiler.

```python
start = EpicStartService(settings, store, herdr, configured_spec, integration_principal)
coordinator_connection = RuntimeService(store, coordinator_principal, log, epic_start=start)
result = await coordinator_connection.call_async("epic_start", {
    "project_id": configured_spec.project_id,
    "epic_id": configured_spec.epic_id,
    "epic_run_id": integration_principal.epic_run_id,
})
if result.ok:
    principal = start.connection_principal(integration_principal)
    integration_connection = RuntimeService(store, principal, log)
```

Detta är Python-API med explicit serviceinjektion från betrodd launcher. CLI
startar ingen dold loop. F35 ansluter agentdrivna taskverktyg, F36/F37 samlad
överlämning och fullständigt native taskflöde. Utan injicerad startservice
annonseras inte `epic_start`. Worker/Integration nekas start; registrerad
Integration läser och begär tillgängliga taskoperationer endast inom egen epic.
Main-merge är Coordinator-behörighet. Prompt/ACK ger ingen roll. F04:s host,
databas och konfiguration måste skyddas; policy isolerar inte samma OS-användare.
F38:s driftsgate återstår.

## Beständig avsikt och återförsök

Epiclås/SQLite binder ägare, principal, spec, konfiguration och F05-resurs före
Git-effekter. F05 skapar ägd epicbranch från ren aktuell main. EpicRun blir ACTIVE,
utan task eller Worker-slot. Eventuell injicerad F25-synk måste bekräfta initial
Active före F11-runtime. Ingen TeamPlayer-anslutning öppnas implicit.

Journal: PREPARING → START_PENDING → RUNTIME_READY → DISPATCH_REQUESTED → REGISTERED.
Registrering kräver verklig session/process/pane och strikt korrelerad native ACK
från samma Codex-tråd. Prompten är högst 65536 UTF-8-bytes och innehåller policy,
aktuell branch/path/bas/HEAD, hela spec och registrerad principal.

Uppdrag/hash/baseline/correlation och ursprunglig 45-sekundersdeadline sparas före
enda dispatch. Retry/reopen observerar kända resurser, utan blind omsändning eller
förlängd deadline. Avbrott precis före dispatch kan lämna oskickad avsikt för
avstämning. ACK_TIMEOUT och ändrat ägarskap/spec/session/process/HEAD bevarar
journalen och stoppar registrering. Operatören läser faktisk avsikt och åtgärdar
kvarvarande steg. Ingen adoption, reset, cleanup eller fabricerad session.

Registrerad epicbranch får avancera genom verifierad integration, men ursprunglig
uppdragscommit måste finnas kvar i historiken. Annan run/principal får inte ersätta
ägaren. Separat F13-stopp är inte epic-Done.

## Faktiskt native prov 2026-10-09

[Verifieringsdata](F-34-native.json) binder tjänsternas exakta filhashar till riktig
Herdr0.9.3/Codex0.162.0-session. Egen server `hc-f34-20261009`, ny F05-EpicRun,
separat SQLite och F04-profil i tidigare godkänt ofarligt repo; inga adopterade runs.
Session `01a121bf-e620-7081-97e1-e92adad365fe` tog emot helt uppdrag och gav exakt
INTEGRATION_READY. SDK-start/reopen återanvände samma EpicRun/workspace/agent och
enda native användarturn. SDK nekade främmande epic, Integration-start och
main-mergepolicy. Noll tasks/Worker-slots. TeamPlayer-synk var inte injicerad här;
initial pending/återförsök täcks av kontrollerat serviceprov.

F13-stopp `ff9646b7-c563-4564-ad7a-e79b6b4d5207` återlästes fysiskt STOPPED innan
enbart egen server stoppades. Branch/worktree/SQLite/historik och gamla fixtures
bevaras. Fixtureepicen är ACTIVE utan main-merge. Nativehjälparens path- och
serialiseringsfel rättades med återläsning av känd start/stopp, utan extra dispatch
eller stoppmutation. Ingen tillits-, update- eller säkerhetsdialog besvarades.
