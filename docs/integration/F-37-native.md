# F37: verkligt Integration-styrt epicprov

## Förberett prov 2026-10-10 (historiskt före start)

F37 är InProgress3/User105, lokal Active, acceptans 0/3. E09 är Active8,
egenacceptans 0/3. Detta dokument intygar ännu ingen nativeframgång.

Utvecklingsbranch `task/e09-f37` utgår från granskad och integrerad F36 på
E09 `517530ab9057a5a1e87179b37b3e2e3c51a1460a`. Projektets main är
`73f64767b6196d5b0786b41b6dbdf39b8a8f5fe7`.

| Resurs | Faktisk identitet |
| --- | --- |
| Lokal projekt/epic | `f37-probe-r1` / `f37-epic-r1` |
| EpicRun-intention | `54311f5a-9556-4d6f-ba13-6ac6a1a5f973` |
| Egen namngiven Herdr-server | `hc-f37-r1-20261010` |
| TeamPlayer-testepic | `c3309a13-5350-4d19-b668-592cac9eadc3` |
| A | `b224e9c0-de87-4739-91ee-e65a9b792f66` |
| B | `535e110e-b2cc-41c9-a7f9-46d348443f18` |
| C, beroende av A | `455fb603-5099-42ef-b8ee-f587f9c802ea` |
| Tilldelning samtliga tasks | User `105f26a7-0648-438d-94fd-3260ac3af4ee` |
| Ofarligt godkänt fixture-main | `d4215a26ba5e77d64a1fd4aed4117e5313c43563` |

Epic och tasks har skapats med sparade idempotensnycklar och återlästs.
Alla tre tasks är Pending1 med rätt User-ägare och C:s exakta A-beroende.
Den nya servern är före start bekräftat avstängd. Inga produktworktrees,
sessionsidentiteter, Worker-reservationer eller merger intygas ännu.
F35:s avslutade r5 adopteras eller återöppnas inte.

## Körning och beslutsgränser

[Provvärden](../../tests/native_f37_host.py) bygger de riktiga servicesen,
färsk autentiserad TeamPlayer-anslutning per MCP-anrop och F36:s konfigurerade
`epic_complete`. Skyddad operator-konfiguration ligger utanför repot. Den
registrerade Integration-sessionen får endast sitt scope via scoped MCP;
Workers skriver, testar och committar bara sin egen task.

[Operatörsverktyget](../../tests/native_f37_probe.py) kan starta F34:s egen
run, skicka en sparad kontrollprompt, observera, oberoende läsa ett aktuellt
handoff och slutligen stoppa den egna Integration-sessionen. Det gör inga
taskstarter, taskreviews, taskmerger eller `epic_complete` åt agenten.
Promptintention sparas före sändning. Okänt utfall kräver återläsning av
originalsessionen; samma prompt skickas inte blint igen.

Integration ska först få C-start nekad före A-Done, sedan starta A/B med högst
två reserverade Workers. Ärvt AGENTS kräver saknat mänskligt prefix: verklig
BLOCKED/Attention, bekräftad parkering och återupptagning i samma SID med
explicit mänsklig input. Ingen förvald eller antagen input används. A:s
avsiktliga första README-utelämnande ska nekas i full review och korrigeras
i ursprunglig Worker-session. C får därefter starta från integrerat A.

Samtliga tasks kräver aktuellt fullständigt reviewunderlag, godkännande,
faktisk no-ff-merge, eftertest, fysiskt stopp, slotrelease och färsk
TeamPlayer-Done. Därefter kör F36 separata build-, unittest- och
[semantiska acceptanskommandon](../../tests/native_f37_acceptance.py).
Den sparade EPIC_READY-rapporten måste stämma med verklig Git, SQLite,
sessionhistorik och board. Båda main-branches ska vara oförändrade.

## Faktisk start och nuvarande hinder

Kodcheckpoint `5397745e8e8024640b9308ecf91b4e48281bcbca` har ruff- och
py_compile-resultat exit0. Ny egen server startades med sparad intention,
PID2926419 och faktisk process-startidentitet; Herdr0.9.3/protokoll22
bekräftar running/compatible. F34 registrerade den nya runen med faktisk
original-ACK och SID `01a126e7-6860-7e13-b3c8-0aeaa451f30e`, Integration
`hc-ba6d43f8214f4b389db4d9c5`, pane `w1:p1`, terminal
`term_65d7ff7da9ecd1`. Startoperation
`6b6bb065-47a0-4843-88cb-cde15897aa92` är REGISTERED.

En kontrollintention sparades före en enda faktisk prompt. Integration
begärde själv `task_start` för C. Verklig GitKraken PermissionRequest
återlästes för rätt SID/cwd och exakt projekt/epicrun/f37-c; därefter visar
native dialogen Allow, Allow for this session, Always allow och Cancel.
GitKraken CLI är3.1.76. Herdr kan visa working under den väntande hooken;
GitKrakens faktiska turn-state är blocked/PermissionRequest. En hook är
ingen utförd taskstart. SQLite har ännu0 tasks/Workers/merger och både
fixture-main och projekt-main har återkontrollerats oförändrade.

F37 är NeedsApproval5/User105, lokal Attention, acceptans0/3.
Användaren har tillfrågats om enbart nya F37-r1-sessionens scoped
verktyg. R5:s tidigare godkännande återanvänds inte. Herdr-skillen kräver
ett mänskligt beslut innan operatören besvarar dialogen. Även frågan om
mänskligt prefix väntar på faktiskt svar. Nästa ansvariga är användaren
för dessa beslut, därefter operatören för den verifierade dialogen och
Integration för alla taskbeslut i originalsessionen. Server/session,
SQLite, worktree, refs och intentioner bevaras. Ingen blind replay.
[Maskinstatus](F-37-progress.json) redovisar nuvarande läge.

## Kvarvarande verifiering

MCP-beslut, park/resume, samma-SID-fix, tre
reviewade leveranser och komplett handoff återstår. Prefixfrågan till
användaren väntar på svar. R5:s verktygsgodkännande gäller endast r5;
eventuell faktisk begäran i den nya sessionen behöver ett beslut för den.

Efter säkrad aktuell rapport stoppas egna runtimes genom F13 och endast
den egna testservern stängs. Worktrees, refs, SQLite och faktisk
sessionhistorik bevaras. Full utvecklingsreview, Task→E09 och relevanta
eftertester återstår före F37-Done. E09 kräver dessutom samlad verifiering,
slutreview, PR/main-merge och slutverifiering.

Det manuella utvecklingsworktreet är bootstrap; inga produktregistreringar
fabriceras. Produktprovet använder nya verkliga registrerade resurser.
Detta prov intygar inte full OS-isolering av credentials; F38 behandlar
den kvarvarande produktgränsen.
