# F45 provplan och extern förutsättning X-03

**Läge 2026-10-29:** TeamPlayer NeedsInput3/User105, lokal Attention; målmaskin ej vald. F44 är Done8, granskad/integrerad i E11no-ff18415ef och12 eftertester PASS. E11 är Active13/egenacceptans2/3; epicbeskrivning återläst. Detta dokument planerar nästa task; ingen F45-kod, provsession, SSH-stängning eller maskinomstart har startats.

## Uppgift som saknas

Ange en separat isolerad testmaskin med värd/SSH-adress, eller välj uttryckligen denna Pi som målmiljö. Backlog X-03 och F45.A3 kräver verklig målmaskinomstart. En container-/processomstart räknas inte som maskinomstart. En fråga om målmiljön är ställd till användaren; svar saknas. Nästa ansvariga roll: operatör/användare väljer miljö, därefter bootstrap Worker genomför F45 i task-worktree från aktuell E11.

## Avgränsning när miljön är vald

Eget ofarligt testrepository, SQLite, skyddad originalkonfiguration, namngiven Herdr-server och separata User105-provtasks. Kod-/review-/mergeprober använder avgränsade testfiler. Befintliga projekt, sessions- och slotbevis adopteras inte och inga ersättningsworktrees skapas. Före varje avbrott sparas faktiska run-/session-/process-/slot-ID:n, branch/worktree/base/HEAD, reviewankare, originaloperationsnycklar och aktuell boardversion. Loggar och konsistent SQLite/Git-backup säkras utanför den process som stoppas.

## Avbrottsmatris

| Scenario | Kontrollerat avbrott | Faktiskt underlag efteråt |
| --- | --- | --- |
| Orchestrator | Stoppa den egna provcontrollern vid dokumenterad operationsgräns | Samma SQLite/runs/slots och originalsessioner, färsk monitoranslutning, ingen extra start/merge/prompt |
| SSH | Avsluta bara provets egen SSH-anslutning | Nativearbete/metadata finns kvar eller konkret Attention; återanslutning utan nytt uppdrag |
| Herdr | Starta om bara provets egen namngivna Herdr-instans | Verifierade originalresurser återfinns eller Attention; ingen adoption eller härledd inaktivitet |
| Codex | Terminera bara den registrerade provagentens verifierade processgrupp | Bevarad session/branch/worktree/arbete; fysisk förlust bekräftas, ingen ny Worker/slotrelease utan bevis |
| Målmaskin | Verklig omstart av vald isolerad värd efter säkrad föredata | Ändrat boot-ID/starttid och separat efterinsamling; beständiga filer/SQLite/Git/reviews/intent jämförs, korrekt fortsättning eller Attention |
| Saknad resurs | Flytta en avgränsad provresurs med säkrad originalkopia | Begriplig Attention med exakt resurs/åtgärd, inget ersättningsworktree, arbete/logghistorik bevarade |

Varje scenario ska ha startläge, exakt avbrottspunkt, förväntat återupptagande/Attention, verklig observation och konkret återställningssteg. Tillstånd och Kanban jämförs med faktiska Git-/runtimebevis; inga Done-markeringar enbart från en etikett. Task/mainmerges och okända synkutfall återanvänder originaljournaler; verifierat redan utfört steg upprepas inte.

## Återstart och insamling

Före målmaskinomstart förbereds på den valda värden en beständig efterinsamling och en tydlig återanslutningsväg. För- och efterboot-ID, tidsstämplar, SHA/föräldrar, SQLite-snapshot, sessionsmetadata, stop-/processbevis och boardstatus arkiveras. Om något inte kan bekräftas blir resultatet Attention med ansvarig operatör och nästa åtgärd; saknat Herdr-workspace eller session döljs inte med en ny run. Maskinomstart bokförs först efter verklig efterkontroll.

## Leveransgrind

F45.A1–A3 förblir omarkerade tills motsvarande faktiska prov finns. Därefter Worker READY_FOR_REVIEW → Integration-review av exakt task/epic → no-ff Task→E11 → eftertester → task Done. E11 får Done först efter samlad egenacceptans/slutreview, PR, mainmerge och slutverifiering. Inga senare beroende E12-tasks väljs medan F45/X-03 återstår.
