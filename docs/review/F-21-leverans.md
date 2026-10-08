# F-21: verifierad taskleverans

## Input och roller

`task_merge` aktiveras endast med operatörens reviewkonfiguration, test-argv och explicita Herdr-session. Betrodd Integration-principal krävs inom taskens projekt/epic. Input: `project_id`, `task_run_id`, stabil `request_key` och separat `verification_key` för ett eftertestförsök. Inga caller-SHA, rollargument, testbooleans, stop-/slotbevis eller godtyckliga kommandon accepteras.

F-20:s slutförda approval, autentiserade granskare, hela aktuella F-18-paketet och test-/handoffreferenser återkontrolleras inom Git Managers repo-lås/SQLite-transaktion. Aktuell registrerad Worker måste ha unik slot, samma session/processbindning och vara fysiskt idle/done utan aktiv native turn. Kontrollen upprepas precis före första leveransmerge.

## Beständiga steg

1. `task_merge` parentintent sparar utförarprincipal, approval-ID/nyckel/full digest, context-ID, review-ID, exakta source/target och operatörens command hash/timeout. En pending leverans blockerar annan leverans i epicen. Ändrad utförare eller konfiguration under samma nyckel avvisas.
2. F-07 gör faktisk `--no-ff` Task → Epic med eget intent och operationstagg. Tasken är MERGING/Active. Känd merge återhämtas med samma parent/F-07-nyckel och verifierade parents/ancestry; ingen extra merge eller Worker skapas. Efter merge valideras historiska approvalpins mot den utförda operationen. Den ursprungliga epicbasen behandlas inte som aktuell HEAD igen.
3. `task_delivery_test` sparas PENDING före ett självständigt test-argv på exakt merge-SHA i epic-worktreet. Minimal miljö återanvänds från reviewservicen. Exitkod, faktisk argv, command hash, timeout och source/target/merge refereras. Success kräver samma rena ägda task/epic-worktrees och oförändrad merge efter provet.
4. F-13:s särskilda `stop_delivered_task` återkontrollerar faktisk journalförd merge och lyckat eftertest, före stopp och slotrelease. MERGING bevaras. Samma generation, pane/session/registrerade processer samt faktisk exit/inaktivitet krävs. Okänt stopp, kvarvarande process eller återuppstånden runtime frigör ingen slot och ger inte Done.
5. Repo-lås och SQLite skyddar slutlig kontroll av merge, test och fysiskt stopp. Först därefter övergår MERGING → DONE och parent avslutas atomiskt. Epicen förblir Active.

Schema 2:s befintliga operationsjournal används; inga migrationer eller fabricerade runs behövs. Skyddad operatörsstate är betrodd. Repo-låset samordnar produktservices; externa manuella Git/filsystemskrivare omfattas inte och avvisas vid observerad avvikelse.

## Återförsök och hinder

- `DELIVERY_BUSY` innebär att repo-låsets begränsade väntetid passerat. Återläs och återförsök samma nycklar efter pågående operation; inga ersättningsnycklar eller resurser behövs.
- Samma leveransnyckel återanvänder känd merge och historik även efter processomstart. Annan nyckel får inte dölja en oavstämd leverans.
- Ett misslyckat eftertest sparar exit/fel och merge-SHA, lämnar MERGING med slot/worktree kvar. Samma testnyckel returnerar resultatet utan ny körning. Ett okänt PENDING-test avvisas utan blind resend.
- Begär explicit ny `verification_key` för omverifiering av samma oförändrade merge. Efter ett lyckat sparat test återförsöks endast stopp/Done, även om anropet anger en ny testnyckel.
- Processförlust efter Git, efter test eller efter fysisk exit avstäms genom respektive journal och observerade fakta. Inget falskt Done, ingen ersättningssession eller dubbel merge.
- Ändrad task/epic-HEAD, approvalhistorik eller testreferens efter merge hindrar avslut. Bevara faktisk SHA och arbete; ansvarig Integration måste avstämma orsaken. Förändrad konfiguration kräver dokumenterat operatörsbeslut, inte omskrivning av gammal testhistorik.

## Cleanup och retention

Worktree behålls efter leverans tills ett explicit `TaskMergeService.cleanup(actor, task_run_id, key=...)` görs. Det använder F-09:s ägarskap/merge/history/dirty/ignored/exklusivitetskontroller och en faktisk F-13-inaktivitetsprobe. Endast ägt rent task-worktree tas bort utan force; branches, sessions-/run-/review-/test-/mergehistorik bevaras. Cleanup-fel påverkar inte en redan verifierad Done och får inte leda till upprepad merge eller radering av osäkrat arbete. Native inaktivitet kan kontrolleras efter verifierad worktree-borttagning via den bevarade runtimebindningen.

## Verifieringsgränser

Temporära Git-repositories, SQLite, testprocesser och MCP är verkliga. Runtime-/processadaptrar i serviceproven är kontrollerade. Samlat verkligt Herdr/Codex review/fix/approval/merge/Done-prov följer i F-22. TeamPlayer-produktadapter följer i E-06; långlivad Integration Agent i E-09. F-17:s avgränsade autonomigate inför F-38 kvarstår.
