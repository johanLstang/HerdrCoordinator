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

## Faktisk start och tidigare hinder

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

## Återupptaget prov efter faktiskt beslut

Användaren har uttryckligen godkänt F37-r1-verktygen och prefixet **Hej**.
Beslutsfilen är skapad exklusivt med mode0600; SHA256
`b138f532b649373c6ed416f6d7d8e8cb63eb11900f93077c4b1fa3c1b2c6c715`.
Endast verifierade scoped dialoger i original-SID besvaras med Allow for
this session. F37 är InProgress8/User105, lokal Active, acceptans0/3;
det tidigare hindret ovan är historiskt.

Agentens verkliga C-start gav DEPENDENCY_NOT_DONE. A/B-start gav
TASK_PREREQUISITE_UNVERIFIED: provvärdens betrodda prerequisite_probe hade
inte kopplats till selection/start. Ingen Worker eller taskmerge skapades.
Detta konkreta kompositionsfel rättades i
`6d6526b7492946aac3902671bd80f9d6e950d0b6`, värdhash
`c9469aa994e4733d86cefaa1e79bbd4d7cfa530543fb2293003a54f207a06caf`.
Proben kontrollerar den verkliga skyddade beslutsfilens ägare/mode, hash,
original-SID/run och exakt registrerad taskspecifikation. Test mot verklig
fil accepterade alla tre specs och nekade ändrad goal; Ruff/py_compile exit0.
Tillgänglig mänsklig input verifierar startförutsättningen. Själva svaret
lämnas till Workers separat efter verklig BLOCKED/park, genom InputDecision.

F13 genomförde faktiskt stopp `a0446351-5634-4672-aea1-ab36bbdfa27f`
och resume `75dfe3c6-a5bb-4e30-ae71-2c53ddadafab` av samma
Integration-SID. Fysisk STOPPED, samma run/branch/worktree, oförändrat
taskscope/settings/spec och aktuell registrerad anslutning verifierades.
Inga Worker-identiteter fanns att ändra. Den nya kontrollintentionen
sparades före en enda prompt; originalturnens kända nekanden upprepades
inte blint. Integration har därefter läst färsk overview och valt ny
task-start själv. Värdkällhashen redovisas separat från launchargumentens
fingerprint; inga påståenden om att filbytes ingår i launchfingerprint.

## Faktisk Attention och operatörsingrepp

Följande observation gjordes före återupptagning och leverans. Båda faktiska A/B-blockeringarna har verifierats till
PARKED_AND_SYNCED, fysiskt STOPPED och slotnull; TeamPlayer NeedsInput6
med User105. A SID01a127a6-5150-7c70-bd6e-71342cb66b54/blocker
3e3a5954-0857-445a-bc32-8aabe7195a07; B SID01a127a6-9575-7471-b9e1-657c2a13f231/
blocker c7db356b-f519-42e9-984a-2cae9e7a2341. Före-input-provet har
SHA256003ea0ad62ec3789ce6c79cb11b5cffd0c714de1f2669ebd88e4dcba0c05aefe.
Först därefter levererades det verkliga Hej-beslutet till Integration i
en enda sparad promptintention. Agenten bekräftade Hej, läste de faktiska
blockerar-ID:na genom scoped task_park_blocked och begärde resume_task.
F37-r1:s scoped dialoger är uttryckligen godkända; samma beslut frågas inte igen.

Ett ofullständigt native task_start-anrop efter värdresume saknade
projekt/run/spec. Operatören valde Cancel i den exakta ospecificerade
dialogen. Ingen task skapades. Agenten återläste overview och byggde
själv ett komplett nytt anrop. Detta UI-ingrepp redovisas; inga taskbeslut
eller agentrapporter fabricerades av operatören.

Efter säkrad aktuell rapport stoppades egna runtimes genom F13 och
endast den egna testservern stängdes. Worktrees, refs, SQLite och faktisk
sessionhistorik bevaras. Full utvecklingsreview, Task→E09 och relevanta
eftertester återstår före F37-Done. E09 kräver dessutom samlad verifiering,
slutreview, PR/main-merge och slutverifiering.

Det manuella utvecklingsworktreet är bootstrap; inga produktregistreringar
fabriceras. Produktprovet använder nya verkliga registrerade resurser.
Detta prov intygar inte full OS-isolering av credentials; F38 behandlar
den kvarvarande produktgränsen.

## Verifierat nativeutfall

[Fullständigt maskinunderlag](F-37-native.json) innehåller tre verkliga
DONE-leveranser med aktuella fulla reviews, godkännanden, no-ff-parents,
eftertester exit0, fysiska stopp och User105/TeamPlayer-Done. Integration
utförde 40 scoped native MCP-anrop. C nekades före A-Done och startades
först efter A:s integrerade leverans. Högst två Workers var reserverade.
Alla tre Workers återupptogs efter verklig Attention i original-SID;
A:s numrerade README-korrigering genomfördes i samma Worker-SID och
omgranskades mot aktuell epicbas efter B:s merge. Operatören gjorde inga
taskstarter, scheduling, taskreviews, approvals, taskmerger eller epic_complete.

| Task | Worker-SID | Faktisk Task → Epic-merge |
| --- | --- | --- |
| f37-a | `01a127a6-5150-7c70-bd6e-71342cb66b54` | `4962e96764dc5327836b49149b4619f8ef04e6b8` |
| f37-b | `01a127a6-9575-7471-b9e1-657c2a13f231` | `a0d0f46ee5f51a1106093afa7e335cecc045b18e` |
| f37-c | `01a127b5-0993-73b3-b07e-72b76aec95f8` | `6b8e50a2edbe89cf082184a035f4852413dcc5db` |

F36:s aktuella sparade receipt `10e64ff5-95ad-42fc-b205-f7a516a755da` / `f37-r1-epic-complete-54311f5a-v1` är
EPIC_READY_FOR_REVIEW för epic `6b8e50a2edbe89cf082184a035f4852413dcc5db` mot fixture-main
`d4215a26ba5e77d64a1fd4aed4117e5313c43563`. Manifest `890a7315b08ea22f7d11f21fa35ce01d9474420efeda6dae229b5ff76766b240`, konfiguration
`f61316075daddfcfb41c06cbd51519154a598aaebc5bef2edac9a0743fc8371b` och rapport `066f3f9313f296644ad77170ec7d741b9ebd622c0af5b01800a37aab50ea0044` har jämförts med fulla
sparade data och verklig Git/SQLite/TeamPlayer. Build, unittest och semantisk
acceptans avslutades faktiskt med exit0. Alla tre Worker-runtimes var
STOPPED och samtliga slots fria vid oberoende require_current-verifiering.
Projekt-main `73f64767b6196d5b0786b41b6dbdf39b8a8f5fe7` var också oförändrad.

Native epic_complete-anropet avslutades, men app-servertranskriptets stora
textresultat innehåller en explicit klippmarkör och är inte komplett JSON.
Native slutrapportens ID:n/SHA/hashvärden matchar det fullständiga sparade
kvittot, som oberoende verifierades medan originalprincipal fortfarande
var live. Det fulla sparade kvittot finns i maskinunderlaget; ett komplett
oklippt RPC-transkript intygas inte. Efter slutstopp intygas ingen liveprincipal.

F13 final-stop `4a747a75-914a-4250-a086-a6c2f61291e9` / f37-final-stop är
SUCCEEDED/STOPPED/inactive, följt av fysisk reconnect STOPPED i samma SID.
Endast namngivna hc-f37-r1-20261010 stoppades; faktisk status är not running
och ursprungliga server-PID2926419 saknas. En första lokal statusassert
förväntade texten stopped; återläsning bekräftade verklig not running utan
upprepad stoppoperation. SQLite, worktrees, refs och sessionhistorik bevaras.

## Utvecklingsleverans och nästa grind

F37 är Testing/User105, lokal Active; kriterier slutmarkeras efter
utvecklingsreview, Task → E09 och relevanta efterkontroller. Worker lämnar
READY_FOR_REVIEW med nativeunderlaget och oförändrad testad källcheckpoint
6d6526b7492946aac3902671bd80f9d6e950d0b6. Ruff/build/READMECLI exit0,
wheelns 62 exakta Pythonmoduler och tre byteidentiska policies verifierade.
Den samlade E09-körningen med 892 insamlade tester på denna checkpoint är
ännu inte avslutad; ingen PASS intygas för den innan faktiskt slutresultat.
E09 behåller Active till samlad verifiering, slutreview, PR/main-merge och
slutverifiering. Bootstrapundantaget gäller utvecklingsintegration; den
verkliga produkttestepicen har egna registrerade resurser och mergas inte till main.
