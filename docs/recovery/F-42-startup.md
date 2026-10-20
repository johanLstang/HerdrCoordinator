# F42: läsande startavstämning

## Kontrakt

Källor: arkitektur §§11–14,28,45; workflow §§35–40; Backlog F42.

Avstämningen läser projektets beständiga Coordinator/Epic/Task-runs, Worker-reservationer, operationsjournaler och externa referenser, inklusive väntande operationer som tillhör avslutade runs. Rapporten innehåller endast explicita identiteter, tillstånd, observerade commits och säkra orsakskoder; inga prompts, credentials eller råa adapterfel.

- VERIFIED: samma ägda Git-resurs, kända HEAD, runtime och boardläge har observerats.
- RESUMABLE: känt partiellt eller bekräftat stoppat arbete kräver fortsatt befintlig operation/session. Detta är rådgivning, ingen start eller resume.
- DRIFT: konkret motsägelse, exempelvis saknad ägd worktree, oväntad HEAD, ändrad identitet, fel tilldelning eller Done utan verifierad leverans.
- UNCERTAIN: underlag kan inte läsas eller ändras under avstämningen. Ett timeout är aldrig bevis för att en session är borta.

Rapporten ändrar inte runs, Kanban, slots, sessionsreferenser, Git eller journaler. En andra läsning upptäcker ändrad SQLite/Git/board under provet. Grinden binds dessutom till exakt samma boardögonblicksbild som användes när tasken eller epicen valdes. Ocommittat Worker-arbete bevaras och rapporteras; endast en annan HEAD utan beständigt verifierbart commitunderlag är en commitavvikelse. Slutförda egna brokercommits kontrolleras mot Gitobjekt, förälder, träd och operationsmarkör. Done kräver leveransbevis, inte enbart boardstatus eller en merge-SHA i en agentrapport.

Operatören komponerar betrodda runtimeobservatörer för respektive registrerad run från dess ursprungliga inställningar/Herdrprofil. F13:s läsande observation används för Integration/Worker; Coordinator observeras via sin ursprungliga F38-registrering. Saknad observatör ger UNCERTAIN. Behörighet till dessa objekt kommer aldrig från ett MCP-argument.

Grinden kör en färsk rapport före ny Task-/Epic-reservation i scheduler/Coordinator. Befintliga kritiska Git-, owner-, kapacitets- och branchkontroller kvarstår. Rapportobjekt är ingen återanvändbar behörighet. Oavstämda väntande sidoeffekter stoppar ny scheduling och hänvisar till originalnyckeln; långlivade etablerade scheduler-/epicclaim-registreringar inventeras separat från ofullbordade sidoeffekter. F43/F44 ansvarar för effektfull återanslutning och återställning.

En verifierat PARKED task med fysisk stoppbekräftelse och utan slot kan förbli RESUMABLE medan oberoende arbete startas. Detsamma gäller ett redan skickat input vars ursprungliga beslut, park-/resumehistorik, oförändrade resurscommit, samma Codex-session, aktuella processer/generation och reserverade slot kan verifieras: rapporten hänvisar till observation av det ursprungliga inputkvittot. Ingen input skickas om och ingen resume genomförs av avstämningen. Saknas något av dessa bevis blockerar operationen ny scheduling. Långlivade scheduler-/claim-registreringar redovisas uttryckligen i rapporten; en fasetikett ger ingen runtime- eller slotbehörighet.

En Integrationgrind omfattar sin epic och projektets samtliga Worker-reservationer; Coordinatorgrinden omfattar hela projektet. Rapporten inventerar alltid hela projektet. En osäker separat Coordinatoranslutning blockerar projektets nya epicclaim; den utgör inte i sig bevis för fel i en redan registrerad Integrations egna Worker-resurser.

Integration-observatören stödjer också den verifierade additiva korrigeringsplanen och observerar då originalregistreringen. Ingen session eller ursprunglig konfiguration skrivs om. Rapporten hålls internt för projektet; Integrationens MCP-projektion visar endast den egna epicens resursidentiteter och anonyma koder för andra kapacitetshinder.

CLI utan registrerade TeamPlayer/runtimeobservatörer kan inte aktivera automatisk scheduling. `--check` förblir ren konfigurationsvalidering. Manuell utvecklingsbootstrap registreras inte som en produktkörning.

## Verifiering

Pågår inför slutreview och faktisk Task → E11-integration. 23 riktade prov passerade före den sista explicita boardbindningen; dess två raceprov och berörd regression körs nu. Omfattning: riktiga temporära Git/SQLite-resurser med uttryckligen simulerad Herdr/Codex/TeamPlayer, återöppnad databas, ändrad HEAD/saknad worktree/falsk Done, timeout och samtidiga stateändringar. F42 gör inget nytt verkligt native- eller maskinomstartsanspråk; F43/F45 verifierar dessa.


### Avstämd fortsättning efter epicleverans

En pågående `coordinator_epic_continuation` får inte generellt ignoreras.
Endast ett claimanrop med journalens exakta ursprungliga claimnyckel får gå
vidare när samma registrerade Coordinator är verifierat aktiv, den länkade
leveransen faktiskt är RELEASED/SUCCEEDED, dess epic har verkliga review-,
Git/main-merge- och sluttestbevis och boarden visar Done med rätt utförare.
Andra nycklar och ofullständiga bevis blockerar nya claims. Avstämningen utför
inte fortsättningen; F40:s befintliga service driver sin ursprungliga operation.

Om en första kapacitetsfyllning stoppas av oavstämt befintligt arbete får F29
fortfarande observera dess ursprungliga Worker-rapport. En avgränsad ny
kapacitetskontroll efter observationen kör hela grinden igen. F42 accepterar
inte en okänd HEAD bara för att ett rapportmeddelande finns.
