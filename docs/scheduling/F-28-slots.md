# F-28: atomiska Worker-platser

`WorkerSlots` är en intern policy som används av F-15 taskclaim, F-11
runtime-start och F-13 resume/stopp. Deras befintliga registrerade aktörs-,
epic-, task- och Gitkontroller sker före reservationen. Inga MCP-argument för
slot, release, roll eller inaktivitet tillkommer.

## D-03 och persistens

`Settings.max_workers` väljer 1 eller 2 platser per projekt. TaskRun.worker_slot
lagras i befintlig validerad JSON, schema 2, utan migration. SQLite
`BEGIN IMMEDIATE` serialiserar färska läsningar och reservationer mellan
serviceanslutningar; nästlade operationer använder savepoints i samma transaktion.
TaskRun:s befintliga unika index för ofullbordad project/task-ägare och
F-15:s stabila operationsnyckel hindrar en andra run för samma task. Ingen
separat sloträknare härleds från Kanban eller bara WORKING.

Claim och slot committas före Git/runtime. Slot och start-/resumeintent committas
före Herdr. CLAIMED, STARTING, arbete, review/fix och okänt externt utfall behåller
kapacitet. Konfiguration som sänks till 1 medan slot 2 fortfarande är reserverad
stoppar ny reservation. Motstridiga claims eller en potentiellt levande run utan
slot ger fel före extern start; policyn reparerar inte journalen automatiskt.

## Frigörande och återförsök

- Ett F-15-fel kan frigöra CLAIMED endast när den egna parentoperationen har
  sparat felet och ingen runtime-startoperation eller native bindning finns.
  Task/run/op/spec/prompt/bas förblir samma. Retry reserverar en ledig plats och
  återanvänder eventuell redan skapad Gitresurs. Ändrad timeout/spec avvisas.
- Efter varje runtime-startintent krävs F-13:s faktiska native stopp. Dess
  EXIT_REQUESTED-journal, aktuella generation och nya process-/paneobservation
  kontrolleras innan slotrelease och STOPPED/SUCCEEDED committas tillsammans.
- Parked/Done utan slot accepteras som inaktiv endast med lyckat READY-startbevis,
  fullständig matchande binding, lyckat stopp för aktuell generation, sparade
  processidentiteter/grupper och ny fysisk inaktivitetsprobe. Pending resume
  spärrar denna bedömning. Done-status eller tom processlista ensam räcker inte.
- Resume reserverar på nytt inom samma transaktion som dess intent och behåller
  exakt Codex-session, branch och worktree. Ny start och resume konkurrerar om
  samma uppsättning platser. Timeout eller osäker stop-/resumeutfall frigör inget.

Policyn omfattar samverkande produktservices. SQLite/Git/processobserver måste
vara skyddade från Worker och externa skrivare enligt D-02/F-38. PID/gruppbevis
ger inte cgroup-isolering eller full sandbox. Manuella utvecklingsworktrees
adopteras inte som produkt-runs.

## Verifiering

`tests/test_worker_slots.py` använder verkliga temporära Git-repositories,
SQLite-filer och konkurrerande trådar med separata serviceanslutningar.
Herdr/Codex/processobserver är kontrollerade adaptrar. Proven täcker tre
överlappande starter, dubbel taskstart, enplatsläge, säkert pre-runtimefel och
retry, osäker workspace-/agentstart, konkurrerande start/resume, motstridig
persistens, otillräckliga stoppbevis och sänkt workergräns.

F-13/F-21-regressionerna provar stopp, parkering, barn/grupper, omstart, resume,
retry och leverans. Faktiska testkommandon, commitpar och integrationsresultat
registreras i Backlog och leveransreview. Native parallellitet med två Workers
verifieras i F-30; denna task påstår inget sådant liveprov.
