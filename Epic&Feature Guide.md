# HerdrCoordinator guide för epics och features

Den här guiden används för att bryta ned HerdrCoordinator i epics och implementerbara features. En epic beskriver ett sammanhängande resultat för den som driver ett utvecklingsprojekt genom Herdr. En feature är en avgränsad leverans som kan byggas, granskas och verifieras med tydliga kontrakt, tillstånd och agentbehörigheter.

Guiden är projektets planeringsmall. Den innehåller exempel och rekommenderad leveransordning; en faktisk backlogg skapas separat och ska visa mål, epics, features, prioritet, beroenden, acceptans, nästa steg och en avslutande Kanbanöversikt.

Utvecklingen av HerdrCoordinator följer [Utvecklingsprocess.md](Utvecklingsprocess.md) och [AGENTS.md](AGENTS.md): en feature/task i taget med aktuell tilldelning och status från TeamPlayer. Produktens två Workers är ett separat implementations- och verifieringskrav. Task-worktrees och Task → Epic → main-integration gäller i båda flödena.

## Styrande underlag och ansvar

- [Herdr_Workflow.md](Herdr_Workflow.md) styr agentroller, branchägarskap, task-livscykel, review, merge och betydelsen av `Done`.
- [Arkitektur och implementationsplan](Arkitektur%20och%20implementationsplan%20%E2%80%93%20Herdr%20-Codex%20Multi-Agent%20Workflow.md) styr komponentgränser, runtime-modeller, interna tillstånd och implementationsfaser 1–12. Hänvisa till avsnittsnummer och rubrik i varje feature.
- Denna guide styr hur kraven beskrivs och bryts ned. Den ersätter inte arbetsprocessen eller arkitekturen.
- TeamPlayer-projektets Kanban är primär källa för arbetsstatus. Backloggens statusöversikt speglar TeamPlayer när kopplingen finns. Före kopplingen anges uttryckligen att statusen är lokal planering och att TeamPlayer-ID saknas.
- SQLite lagrar teknisk runtime-information, till exempel runs, sessioner, worktrees, reviewhistorik och commits. Git visar vilket arbete som faktiskt är committat och integrerat. En statusrad eller agentrapport räcker inte som bevis för merge.

Vid motsägelse används arbetsprocessen för roller och leveransregler och arkitekturplanen för tekniska kontrakt och fasordning. Exempelvis används `CLAIMED` och `PARKED` enligt arkitekturplanens §§15–16; workflowets `ASSIGNING` är inte ett extra Kanban-tillstånd. Om motsägelsen inte kan lösas med denna uppdelning ska den dokumenteras med ett konkret beslut som krävs innan den berörda operationen implementeras.

## Projektets utgångspunkter

- HerdrCoordinator är en deterministisk orchestrator runt Codex, Herdr, Git och TeamPlayer. Agenter föreslår och granskar arbete; orchestratorn validerar och utför kritiska operationer genom avgränsade adaptrar.
- Arkitekturplanen rekommenderar Python 3.12+, SQLite, `asyncio`, Pydantic och MCP Python SDK. Valet mellan `sqlite3` och SQLAlchemy samt exakt paketstruktur ska dokumenteras när grundplattformen byggs. Strukturillustrationerna i planen är exempel.
- Epic Coordinator äger `main` och slutreview av epics. Epic Integration Agent äger en epic och dess task-review. Worker Agent implementerar en task i eget worktree, testar och committar.
- Git Manager utför kritiska Git-operationer. Integration Agent får begära Task → Epic-merge; Coordinator får begära Epic → `main`-merge. Worker får inte mergea, ändra andra worktrees eller skriva Kanban-status direkt.
- Epic-branch heter `feature/epic-<epic-id>`. Task-branch heter `task/<epic-id>-<task-id>` och skapas från aktuell epic-branch. Varje aktiv branch har eget worktree. Verkliga ID:n och paths ska komma från projektkonfiguration och taskkopplingar.
- MVP omfattar ett repository, en aktiv epic, högst två aktiva Workers, epicstatusarna `Planned`, `Active`, `Done` och taskstatusarna `Planned`, `Active`, `Attention`, `Done`, task-review och merge samt epic-review och merge. Flera samtidiga epics, fler Workers, arbete över flera repositories, automatisk konfliktlösning på epicnivå och dynamisk agentskalning ligger efter MVP.
- En blockerad session kan parkeras utan att förbruka en aktiv Worker-slot. Återupptagning använder samma session, branch och worktree när en slot finns. En saknad session eller worktree ska hanteras uttryckligen, inte döljas med en ny körning.
- Exakta Herdr-, Codex- och TeamPlayer-anrop ska verifieras mot tillgängliga gränssnitt när respektive adapter byggs. Verktygsnamnen i arkitekturplanen beskriver logiska operationer och är inte bevis för att externa API:er har dessa namn.

## Epic, feature och TeamPlayer-task

Använd stabila lokala ID:n, exempelvis `E-01` och `F-01`, och lagra verkliga TeamPlayer-ID:n separat. Ett exempel-ID i denna guide innebär inte att en task eller epic finns i TeamPlayer.

En körbar feature motsvarar normalt en TeamPlayer-task under sin epic och ett Worker-uppdrag. Om featuren kräver flera självständiga Worker-leveranser ska den delas i mindre features med egna ID:n, beroenden och acceptanskriterier. Inför ingen extra runtime-nivå mellan EpicRun och TaskRun enbart för att planeringsdokumentet använder ordet feature.

Varje task ska innehålla konkreta arbetsinstruktioner, kravkällor, berörda komponenter, kontrakt, beroenden, externa förutsättningar och verifieringssätt. Acceptanskriterier ska även finnas i TeamPlayers avsedda fält. Dokumentera kopplingen mellan lokala ID:n, TeamPlayer-ID:n, branch, worktree och runtime-run.

Projektets TeamPlayer-ID, konton och utförartilldelning ska verifieras vid anslutning. HerdrCoordinator använder projekt-ID `d2ee4c75-7b80-465f-83ac-1750854a8e80` och `blitterbot@gmail.com` som utförare för backloggens tasks enligt användarens tilldelning. Kontrollera användar-ID med get_me och jämför mot taskens executionOwnerKind och responsibleUserId. Runtime-Worker, administrativ utförare och granskningsroll beskrivs separat.

## Prioritet, körbarhet och status

| Prioritet | Betydelse i HerdrCoordinator |
| --- | --- |
| P0 | Grund eller skydd som blockerar säkert genomförande: persistens, tillstånd, rollgränser, worktree-isolering, idempotens och mergevillkor. |
| P1 | Funktion för första kompletta flödet: Herdr/Codex-start, review/fix-loop, TeamPlayer, två Workers, Attention, epic-integration, Coordinator och recovery. |
| P2 | Fördjupad driftsäkerhet och felsökning efter kärnflödet, exempelvis utökad observability och felinjektion. |
| P3 | Senare förbättringar och utökningar utanför MVP. |

Prioritet ersätter inte beroendeordning. Minsta skydd för exempelvis dubbla starter och felaktig merge ska finnas när operationen införs; fas 12 fördjupar skydden och verifieringen.

Epics använder endast följande leveransstatusar, med Coordinator som ansvarig:

| Epicstatus | Villkor |
| --- | --- |
| `Planned` | Epicens arbete har inte startat, även om den inväntar beroenden. |
| `Active` | Epicen är påbörjad och ännu inte slutlevererad. Implementation, review, korrigering, paus, hinder och väntan på main-integration/slutverifiering ingår. |
| `Done` | Alla tasks är Done, samlad acceptans och slutreview passerar, main-merge och slutverifiering är genomförda. |

Epics får inte Attention. Hinder och nästa åtgärd dokumenteras separat medan en påbörjad epic behåller Active. Alla tasks Done gör inte automatiskt epicen Done. Interna EpicRun-faser fram till DONE speglas som Active; PLANNED speglas som Planned. En Done-epic återöppnas till Active endast efter dokumenterat beslut om nytt arbete.

Tasks använder följande Kanban-statusar:

| Status | Villkor |
| --- | --- |
| `Planned` | Arbetet är planerat och inte startat. Väntan på beroende kan behålla denna status. |
| `Active` | Worker har startat i rätt worktree och bekräftat arbetet, eller tasken är i review, korrigering eller merge. En epic är aktiv när Coordinator startar den. |
| `Attention` | Extern input eller åtgärd krävs. Ange orsak, vad som behövs, ansvarig roll och hur arbetet återupptas. |
| `Done` | Tasken är granskad, mergad till epic-branchen och relevant integrationsverifiering passerar. Epicen är slutgranskad, mergad till `main` och slutverifierad. |

**Körbar: ja/nej** är en separat planeringsuppgift, inte en Kanban-status. En task är körbar när krav och acceptans är tydliga, externa förutsättningar finns och blockerande taskberoenden är granskade och mergade till epic-branchen. Beroenden till tidigare epics kräver verifierad merge till `main`.

Interna tasktillstånd följer arkitekturplanens §§15–16:

| Intern status | Kanban |
| --- | --- |
| `PLANNED`, `CLAIMED`, `STARTING` | `Planned` |
| `WORKING`, `READY_FOR_REVIEW`, `REVIEWING`, `CHANGES_REQUESTED`, `APPROVED`, `MERGING` | `Active` |
| `BLOCKED`, `PARKED` | `Attention` |
| `DONE` | `Done` |

Integration Agent ansvarar för taskstatus och Coordinator för epicstatus; orchestratorns TeamPlayer-adapter utför skrivningarna. Ett misslyckat statusanrop ska kunna återförsökas utan att redan utförd Git-merge eller sessionstart upprepas. Redovisa verifierade kriterier och underlag, inte uppskattad procent färdigt.

Vid manuell utveckling utför bootstrapansvarig dessa rolluppgifter enligt Utvecklingsprocess.md. För tasks använder TeamPlayer API Pending för Planned och InProgress/Testing för Active. NeedsReview ligger i taskens Attention; vanlig review/fix-loop ligger kvar i Active. Board epics har status/version i `list_epics`. Coordinator använder `update_epic_status` med färsk version och mappar Pending → Planned, InProgress → Active, Done → Done; återläs och spegla samma status i backloggen. Epics saknar eget utförarfält.

## Epicmall

```md
## Epic E-XX: <Verifierbart projektresultat>

**Implementationsfas:** <1–12 enligt arkitekturplanen; ange flera om det behövs>
**Prioritet:** P0/P1/P2/P3
**Kanban-status:** Planned/Active/Done
**TeamPlayer Epic-ID:** <Verifierat ID eller ej skapat>
**Källa:** <Fil, avsnittsnummer och rubrik>

### Resultat och motiv
<Vad kan projektets operatör eller agentflöde göra när epicen är klar?>

### Omfattning
Ingår: <Flöden, komponenter och integrationer>.
Utanför: <Konkreta avgränsningar>.

### Ingående features och ordning
| ID | Feature | TeamPlayer Task-ID | Beroende | Leverans/resultat |
| --- | --- | --- | --- | --- |
| F-XX | <Namn> | <ID eller ej skapat> | <ID eller inget> | <Verifierbart resultat> |

### Gemensamma kontrakt och regler
<Roller, tillstånd, branch/worktree, persistens, slots, återförsök och felhantering.>

### Risker och öppna beslut
| Fråga | Konsekvens | Åtgärd eller beslutspunkt |
| --- | --- | --- |
| <Fråga> | <Påverkan> | <Nästa verifierbara steg> |

### Epicacceptans
- [ ] <Sammanhängande normalflöde med förväntat resultat.>
- [ ] <Relevant fel-, samtidighets- eller återstartsfall.>
- [ ] <Bevis att roll- och mergegränser upprätthålls.>

### Definition av Done
- [ ] Alla ingående tasks är Done och integrerade i epic-branchen.
- [ ] Samlad build, relevanta tester och epicacceptans passerar.
- [ ] Coordinator har slutgranskat den aktuella diffen mot main.
- [ ] Epicen är mergad till main och slutverifieringen passerar.
- [ ] Statuskällan och dokumentöversikten är uppdaterade med verifiering och merge-SHA; TeamPlayer uppdateras när kopplingen finns.
```

Exempel på en epic är **”Genomför och integrera en task med review och korrigering”**: operatören kan lämna en avgränsad task som Worker implementerar i eget worktree, Integration Agent granskar och orchestratorn integrerar först när aktuella mergevillkor är uppfyllda.

## Featuremall

Beskriv ett sammanhängande resultat. En separat teknisk feature är rimlig när den har ett eget verifierbart kontrakt, exempelvis atomisk claim av en task eller säker merge av en granskad commit. Anpassa omfattningen till en Worker-leverans.

```md
## Feature F-XX: <Tydligt namn>

**Epic:** E-XX <Namn>
**Implementationsfas:** <1–12>
**Prioritet:** P0/P1/P2/P3
**Kanban-status:** Planned/Active/Attention/Done
**Körbar:** Ja/Nej — <Villkor eller blockerare>
**TeamPlayer Task-ID:** <Verifierat ID eller ej skapat>
**Berör:** Domän / orchestrator / Git / Herdr / Codex / TeamPlayer / SQLite / MCP / prompts
**Kravkälla:** <Fil, avsnittsnummer och rubrik>

### Arbetsinstruktion för Codex
<Konkreta steg, uppgiftens gräns och vad Worker ska leverera.>

### Syfte och resultat
<Vem initierar vad, och vilket observerbart resultat uppstår?>

### Funktionella krav
- [ ] <Konkret beteende.>
- [ ] <Fel- eller gränsfall.>

### Flöde och tillstånd
1. <Tillåten roll, starttillstånd och indata.>
2. <Validering och operationer i ordning.>
3. <Beständigt resultat, Kanban-status och agentrapport.>
<Ange återförsök, avbrottspunkter och hur partiellt genomförda operationer hanteras.>

### Data och persistens
<EpicRun/TaskRun/Review, identiteter, session-ID, branch/worktree, commits,
tidsstämplar, schemaändringar och vilka fält som måste vara beständiga.>

### Kontrakt och komponenter
| Del | Nytt eller ändrat kontrakt | Ansvarig komponent |
| --- | --- | --- |
| MCP/domän | <Operation, schema, roll, förvillkor, resultat och fel> | <Komponent> |
| Adapter | <Verifierat gränssnitt eller dokumenterat logiskt kontrakt> | <Komponent> |
| Persistens | <Transaktion, unik nyckel, återförsök eller migration> | <Komponent> |

### Behörighet och isolering
<Vem får begära och utföra operationen? Hur valideras projekt, epic, task,
branch och worktree? Hur skyddas credentials och andra sessioner?>

### Idempotens, samtidighet och spårbarhet
<Vad händer vid dubbelt anrop, fulla slots, föråldrad review eller avbrott?
Vilka run-, session- och commit-ID:n finns i logg och granskningsunderlag?>

### Beroenden och avgränsningar
| Beroende | Status/underlag | Vad krävs för körbarhet? |
| --- | --- | --- |
| <Feature/beslut/extern tjänst> | <Verifierat läge> | <Mätbart villkor> |

### Verifiering och acceptans
- [ ] <Givet/när/så för normalfall.>
- [ ] <Givet/när/så för relevant fel, dubbelt anrop eller omstart.>
- [ ] <Roll-, isolerings- eller samtidighetsfall där det är relevant.>
<Ange fixture, temporärt Git-repository, adaptertest eller manuellt prov som
verifierar kriterierna. Ange vilka externa integrationer som faktiskt körts.>

### Klart för Worker-överlämning
- [ ] Relevanta ändringar är committade i tilldelad task-branch.
- [ ] Relevanta tester passerar och resultat är dokumenterade.
- [ ] Kontrakt, schema, prompts och dokumentation är uppdaterade där de påverkas.
- [ ] READY_FOR_REVIEW anger task-ID, branch, commit, tester och begränsningar.

### Definition av Done
- [ ] Tasken är synkroniserad mot aktuell epic och därefter granskad och godkänd.
- [ ] Orchestratorn har verifierat mergevillkor och integrerat tasken i epic-branchen.
- [ ] Relevanta integrationstester passerar; merge-SHA och status är registrerade.
```

Skriv ”ej relevant” med en kort motivering för delar som inte påverkas. Ett rapporterat `READY_FOR_REVIEW` är en överlämning; det ändrar inte tasken till `Done`.

## Kompakt featuremall

Använd denna när kontrakten redan är tydligt dokumenterade. Länka till specifika avsnitt och behåll explicita acceptanskriterier.

```md
## Feature F-XX: <Namn>

**Epic/fas/prioritet:** E-XX / <1–12> / P0–P3
**Kanban-status/körbar:** <Planned/Active/Attention/Done> / <Ja/Nej och villkor>
**TeamPlayer Task-ID:** <ID eller ej skapat>
**Källa:** <Fil och avsnitt>
**Beroenden:** <ID, verifierad merge eller beslut>

**Arbetsinstruktion för Codex:** <Steg, leverans och tydlig uppgiftsgräns.>

### Resultat och kontrakt
- <Observerbart resultat; indata, roll, förvillkor och utdata.>
- <Tillstånd, persistens, fel, återförsök och isolering.>

### Acceptans
- [ ] <Normalfall med förväntat resultat.>
- [ ] <Relevant fel-, omstarts- eller samtidighetsfall.>
- [ ] <Roll- eller mergevillkor om relevant.>

### Verifiering och leverans
<Prov som bevisar kriterierna, granskad commit och integrationens merge-SHA.
Worker lämnar READY_FOR_REVIEW; Done kräver godkänd review, merge och tester.>
```

## Exempel på körbar featurebeskrivning

Följande illustrerar formatet och är inte en skapad TeamPlayer-task eller ett färdigt API-schema.

**Feature:** F-EX — Starta en Worker för en tilldelad task.  
**Epic/fas/prioritet:** E-EX / fas 4 / P1.  
**Källa:** Arkitekturplanen §§19–20 och §38; Herdr_Workflow.md §§10–12 och §15.  
**Beroenden:** Verifierad grundpersistens, Git-adapter och Herdr/Codex-adapter från faserna 1–3. Fas 4 verifierar en Worker; TeamPlayer-skrivningar införs i fas 6.

**Arbetsinstruktion för Codex:** Implementera startflödet för en explicit angiven task. Validera kopplingen till epic, reservera körningen, skapa task-branch och worktree från aktuell epic, starta sessionen och skicka uppdraget. Persistéra runtime-ID:n och registrera startbekräftelsen. Lämna automatisk review, merge och val av nästa task till senare features.

Acceptans:

- [ ] Givet en giltig task och tillgänglig slot skapas en branch från epicens aktuella commit, ett isolerat worktree och en Worker-session med rätt uppdrag.
- [ ] Tasken går till `WORKING` först efter Worker-bekräftelse. Vid TeamPlayer-koppling motsvarar detta `Active`.
- [ ] Ett upprepat startanrop för samma task returnerar befintlig körning och skapar ingen extra branch, worktree eller session.
- [ ] Ett fel efter worktree-skapande men före startbekräftelse lämnar ett spårbart läge för återförsök eller Attention och redovisar skapade resurser; tasken registreras inte som arbetande.
- [ ] En Worker kan inte använda startoperationen för att starta en annan task.

Verifiera i ett temporärt Git-repository med kontrollerad Herdr-adapter som kan simulera startfel, dubbelt anrop och saknad bekräftelse. Kör även ett separat verkligt Herdr/Codex-prov när integrationen finns; ett adaptertest bevisar inte den externa integrationen.

## Krav som ofta glöms

| Område | Kontrollfråga för varje berörd feature |
| --- | --- |
| Roller | Validerar orchestratorn anroparens rätt att starta, granska, återuppta eller mergea? |
| Git | Kontrolleras branch, worktree, rent arbetsläge, aktuell HEAD och rätt merge-riktning? |
| Review | Avser godkännandet exakt den task-commit och epic-commit som ska integreras? Kräver ändrade commits ny verifiering? |
| Tillstånd | Sparas runtime innan fortsatt automation, och skiljs agentrapport från verifierat resultat? |
| Slots | Räknas och reserveras aktiva Workers så att även samtidiga starter och återupptagningar håller gränsen? |
| Beroenden | Krävs godkänd merge till epic, och till main för föregående epics, innan beroende arbete startas? |
| Attention | Sparas orsak och inputbehov, parkeras sessionen och frigörs slotten utan att worktree eller session-ID förloras? |
| Återförsök | Kan start, merge och Kanban-synk återförsökas efter partiell framgång utan dubbla sidoeffekter? |
| Recovery | Jämförs SQLite, Git, Herdr och TeamPlayer, och hanteras saknade resurser med Attention? |
| Spårbarhet | Kan beslut, reviewfeedback, tester, sessioner och merge-SHA härledas till rätt project/epic/task/run? |
| Credentials | Hålls hemligheter utanför repository, loggar och agentprompts? |
| Cleanup | Tas resurser bort först när arbete och verifieringsunderlag är säkrade och ingen annan körning använder dem? |

## Instruktion för kodningsagent

Denna mall avser ett Worker-uppdrag när projektets orchestrering är etablerad. Tidiga manuella implementationer ska ange tilldelad branch och vilka runtime-delar som ännu saknas. En planerad framtida orchestrator är inte ett redan tillgängligt verktyg.

```md
Du arbetar i HerdrCoordinator. Implementera feature <F-XX: namn> för epic <E-XX>.

TeamPlayer Task-ID: <Verifierat ID eller ej kopplat>
Tilldelad branch/worktree: <Branch och absolut path>
Bas: <Epic-branch och commit>

Läs featurebeskrivningen, beroenden, Herdr_Workflow.md och relevanta avsnitt i
Arkitektur och implementationsplan – Herdr -Codex Multi-Agent Workflow.md.
Följ tillämpliga repositoryinstruktioner om sådana finns.

Mål och acceptanskriterier:
<Klistra in featuremål och verifierbara kriterier.>

Projektregler:
- Arbeta inom tilldelad task och worktree; ändra inte andra agenters arbete.
- Byt inte branch och mergea aldrig som Worker.
- Låt orchestratorn validera kritiska operationer och adaptrarna utföra dem.
- Skriv inte TeamPlayer-status direkt; rapportera till Integration Agent.
- Behåll kopplingen mellan task, run, branch, worktree, session och commit.
- Rapportera konkret blockerare och nödvändig input när arbetet inte kan fortsätta.

Arbetssätt:
1. Läs befintlig kod och tester. Skapa bara den struktur featuren behöver.
2. Kontrollera kontrakt, beroenden och rollgränser; dokumentera nödvändiga beslut.
3. Implementera en avgränsad leverans och relevanta fel-/återförsöksfall.
4. Kör meningsfull verifiering för berörda risker och kontrakt.
5. Committera relevanta ändringar och lämna READY_FOR_REVIEW med task-ID,
   branch, commit, testsammanfattning, ändrade filer och kända begränsningar.
```

## Granskningslista

- [ ] Featurens kriterier och styrande källor är uppfyllda.
- [ ] Worker, Integration Agent och Coordinator håller sina roll- och worktreegränser.
- [ ] Externa adapterkontrakt har verifierats där integrationen införs.
- [ ] Tillstånd, persistens, återförsök och partiellt genomförda operationer är definierade.
- [ ] Dubbla starter och fulla slots hanteras utan parallella ägare till samma task.
- [ ] Review och tester gäller aktuella commits; merge sker i rätt riktning.
- [ ] Tasken sätts till Done efter integration och tester; epicen efter merge till main och slutverifiering.
- [ ] Attention och recovery bevarar kopplingar och redovisar saknade resurser.
- [ ] Tester bevisar relevanta risker; verkliga integrationer skiljs från simulerade adaptrar.
- [ ] Schemaändringar, driftinstruktioner och verifieringsunderlag är uppdaterade där de påverkas.

## Rekommenderad epicordning

Ordningen följer arkitekturplanens faser. Ett fasnummer är inte automatiskt ett epic-ID; en fas kan behöva flera epics eller features med egna acceptanskriterier.

| Fas | Leveransresultat | Underlag |
| --- | --- | --- |
| 1 | Orchestratorn kan starta, persistéra EpicRun/TaskRun och validera tillstånd. | §35 Foundation |
| 2 | Epic- och task-worktrees kan skapas, synkroniseras och integreras genom Git-adaptern. | §36 Git automation |
| 3 | En session kan startas i rätt worktree genom Herdr/Codex-adaptern. | §37 Herdr automation |
| 4 | En explicit angiven task går genom en Worker till commit och READY_FOR_REVIEW. | §38 Worker MVP |
| 5 | Task-review, korrigering och godkänd Task → Epic-merge fungerar. | §39 Review loop |
| 6 | Epics/tasks läses; epics får Planned/Active/Done och tasks kan dessutom få Attention genom TeamPlayer-adaptern. | §40 TeamPlayer MCP |
| 7 | Minst tre tasks genomförs med högst två aktiva Workers och korrekta beroenden. | §41 Två parallella Workers |
| 8 | Blockerat arbete parkeras, frigör en slot och återupptas med rätt session och beslut. | §42 Attention |
| 9 | En långlivad Integration Agent driver epicens scheduling, review och integration. | §43 Epic Integration Agent |
| 10 | Coordinator väljer epic, slutgranskar, mergear till main och väljer nästa. | §44 Epic Coordinator |
| 11 | Avbrott och omstarter återhämtas genom avstämning av runtime och externa resurser. | §45 Recovery |
| 12 | Locks, timeouts, retries, idempotens, mergevillkor, fel och audit härdas systematiskt. | §46 Hardening |

Fas 4 automatiserar inte merge. Fas 5 integrerar tasks innan den fulla Coordinator-loopen finns. Tidiga epics kan därför slutgranskas och integreras manuellt av ansvarig utvecklare i Coordinator-rollen, med samma krav på review, tester och spårbarhet. Detta ska anges i backloggen.

Fas 10 ger enligt planen den första kompletta autonoma versionen. Recovery och hardening ska fortfarande planeras och verifieras för robust drift. Det första sammanhängande provet är en liten epic med tre tasks enligt arkitekturplanens §48, inklusive Worker-överlämningar, task-review, integration, epic-review och merge till `main`.

## Git-flöde för epics och tasks

1. Utgå från aktuell `main` med tillgängliga styrande dokument. Skapa `feature/epic-<epic-id>` och separat epic-worktree. Ange vem som agerar Coordinator och Integration Agent under bootstrap.
2. Skapa varje `task/<epic-id>-<task-id>` från aktuell epic-branch i ett eget worktree. Starta endast körbara tasks och håll MVP-gränsen för aktiva Workers.
3. Worker implementerar, testar, committar och rapporterar `READY_FOR_REVIEW`. Tasken behåller `Active`.
4. Synkronisera tasken mot aktuell epic genom Git Manager. Synkronisering kan föra in epicens ändringar i task-branchen; leveransmerge går Task → Epic. Lös konflikter i task-worktreet och verifiera igen.
5. Integration Agent granskar aktuella commits. Vid `CHANGES_REQUESTED` återgår feedback till samma Worker. Vid `APPROVED` får orchestratorn utföra Task → Epic-merge med `--no-ff` efter validering. Relevanta integrationstester ska passera innan tasken sätts till `Done`.
6. När alla tasks är `Done` kör Integration Agent samlad epicverifiering och lämnar `EPIC_READY_FOR_REVIEW`. Coordinator granskar diff och underlag mot aktuell `main`. Om bas eller commits ändras krävs förnyad verifiering av det ändrade underlaget.
7. Efter `EPIC_APPROVED` och validerade mergevillkor får orchestratorn mergea Epic → `main` med `--no-ff` på Coordinatorns begäran. Slutverifiera innan epicen sätts till `Done`. Registrera merge-SHA och uppdatera statusöversikten.
8. Nästa beroende epic utgår från uppdaterad `main`. Cleanup följer dokumenterad resurspolicy och får inte radera ointegrerat arbete eller nödvändigt verifieringsunderlag.

Vid utveckling av HerdrCoordinator öppnar Coordinator-rollen en GitHub-PR för hela epicen enligt Utvecklingsprocess.md. PR-granskningen kompletterar mergevillkor och ersätter inte kraven på aktuella commits, tester och rätt roll. Produktens framtida automatiserade Git-flöde följer sitt eget kontrakt i arkitekturplanen.

## Arbetsgång för backloggen

1. Läs workflow och arkitekturplan. Skriv epics utifrån verifierbara resultat och koppla dem till faser.
2. Bryt varje epic i självständiga Worker-leveranser. Lägg in en avgränsad förstudie när exempelvis ett externt adaptergränssnitt behöver verifieras.
3. Beskriv acceptans, tillstånd, data, operationer, rollgränser och relevanta fel-/omstartsfall. Ange externa förutsättningar och beroenden med ID:n.
4. Prioritera och bedöm körbarhet separat. Verifiera att parallella tasks inte kräver ännu ointegrerade ändringar från varandra.
5. Vid etablerad TeamPlayer-koppling: skapa eller uppdatera motsvarande tasks, verifiera ID:n och synkronisera innehåll och acceptans. En lokal dokumentändring innebär inte att externa uppgifter är uppdaterade.
6. Efter varje leverans: uppdatera verifieringsunderlag, commit/merge-SHA, blockerare och nästa steg. Läs faktisk status från TeamPlayer för dokumentöversikten.
7. Om ett beslut ändrar workflow eller tekniskt kontrakt, uppdatera rätt styrande dokument och berörda features tillsammans.

## Kanbanöversikt för ett skapat backloggdokument

Avsluta ett komplett backloggdokument med alla epics och features i leveransordning. Ange statuskälla och när översikten stämdes av. `Verifierat` avser uppfyllda acceptanskriterier; en task med alla Worker-kriterier uppfyllda kan fortfarande vara `Active` i väntan på review och merge.

```md
## Kanbanöversikt

Statuskälla: <TeamPlayer-projekt och senaste avstämning, eller lokal planering före koppling>.

| Ordning | ID | Typ | Namn | Epic | Fas | TeamPlayer-ID | Prioritet | Kanban-status | Körbar | Verifierat | Beroende/blockerare | Nästa steg |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | E-XX | Epic | <Namn> | — | 1 | <ID/ej skapat> | P0 | Planned | <Ja/Nej> | <0/N> | <Beroende> | <Steg> |
| 2 | F-XX | Feature/task | <Namn> | E-XX | 1 | <ID/ej skapat> | P0 | Planned | <Ja/Nej> | <0/N> | <Beroende> | <Steg> |
```
