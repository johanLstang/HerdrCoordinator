# Utvecklingsprocess för HerdrCoordinator

Arbetet med HerdrCoordinator sker **en feature/task i taget**. TeamPlayer är källa för aktuell status och tilldelning. [Backlog.md](Backlog.md) styr scope, leveransordning, beroenden och acceptanskriterier. [AGENTS.md](AGENTS.md) gör processen till projektinstruktion för Codex.

Processen gäller när projektets egna features byggs. Produkten ska enligt [arkitekturplanen](Arkitektur%20och%20implementationsplan%20%E2%80%93%20Herdr%20-Codex%20Multi-Agent%20Workflow.md) kunna driva två Workers parallellt; det verifieras i produktens testscenarier. [Herdr_Workflow.md](Herdr_Workflow.md) styr agentroller, task-worktrees och integration. Planeringsformatet följer [Epic och Feature Guide](Epic%26Feature%20Guide.md).

## Projekt och identitet

| Uppgift | Värde |
| --- | --- |
| TeamPlayer-projekt | HerdrCoordinator |
| Projekt-ID | `d2ee4c75-7b80-465f-83ac-1750854a8e80` |
| Utförare | `blitterbot@gmail.com` |
| Verifierat användar-ID | `105f26a7-0648-438d-94fd-3260ac3af4ee` |
| Första kandidat | F-01 Starta ett konfigurerbart Python-projekt |
| F-01 task-ID | `9bc95f85-f05d-4842-b517-c1f8132c49ab` |
| E-01 epic-ID | `0da5c7c4-6e29-475e-aeb6-ce887f3864db` |

Boarden innehåller 12 epics och 50 tasks. Använd lokala E-/F-ID:n i dokumentationen och verifierade UUID:n i MCP-anrop. Epics är organiserande boardobjekt med status/version men utan eget utförarfält. Coordinator håller deras status i TeamPlayer och backloggen överens med verkligt arbete och leveransunderlag.

## Epicstatus: Planned, Active och Done

| Epicstatus | När den används | TeamPlayer API-status |
| --- | --- | --- |
| Planned | Epicens arbete har inte startat, även om beroenden saknas. | Pending |
| Active | Epicen är påbörjad: implementation, taskreview, korrigering, paus, blockerare, slutreview eller väntan på PR/main-integration/slutverifiering. | InProgress |
| Done | Alla tasks är Done, samlad acceptans och slutreview är godkända, main-merge och slutverifiering är genomförda. | Done |

Coordinator sätter Active när epicen startas och senast när första tasken plockas. Active består tills hela leveransgrinden är uppfylld; alla tasks Done räcker inte för epic-Done. Påbörjat arbete går inte tillbaka till Planned vid hinder. Blockerare dokumenteras med orsak, ansvarig roll och nästa åtgärd medan epicen behåller Active. Tasks kan samtidigt vara Attention.

Läs aktuell epic/version med `list_epics`. Skriv med `update_epic_status(projectId, epicId, version, status)` och kontrollera mutationssvaret samt återläs epicen. Vid versionskonflikt eller okänt nätutfall hämtas epicen på nytt före nytt försök. Verktyget är verifierat i TeamPlayers aktuella MCP-katalog; om sessionens lista är äldre ska katalogen uppdateras eller samma autentiserade MCP-anslutning användas direkt. Statusskrivning går till board-epicens UUID, inte `update_task_status` med ett epic-ID.

Aktuell MCP-katalog har inget verktyg för att redigera epicbeskrivningar. Om beskrivningens statustext är äldre, dokumentera avvikelsen och synka texten när en behörig redigeringsväg finns. Använd statusfältet för aktuell status och backloggen för scope och verifieringsunderlag.

Stäm av epicstatus efter varje task, vid hinder och vid review/integration. Intern EpicRun-state PLANNED motsvarar Planned; ACTIVE, READY_FOR_REVIEW, REVIEWING, CHANGES_REQUESTED, APPROVED och MERGING motsvarar Active; verifierad DONE motsvarar Done. Interna faser är inte extra boardstatusar. En dokumentuppdatering startar ingen ny implementationsepic. Nytt arbete i en Done-epic kräver dokumenterat återöppningsbeslut, Active och ny leveransverifiering.

## Kontrollera projekt och board

Kör `list_projects` och kontrollera att HerdrCoordinator har rätt ID och Write-åtkomst. Kör `get_me` och kontrollera autentiserat konto. Läs sedan `list_epics` och `list_tasks` med projekt-ID:t ovan.

Jämför ID, epic-koppling, status, tilldelning, acceptans och beroenden mot backloggen. Vald task ska ha `executionOwnerKind=User` och `responsibleUserId` lika med det autentiserade användar-ID:t. Ett äldre agentfält i tasksvaret är inte utförartilldelningen när executionOwnerKind är User.

Om uppgifterna inte stämmer: dokumentera avvikelsen och rätta kopplingen inom uppdragets omfattning innan implementation. Vid okänt nätverksutfall återläs objektet innan en skrivning upprepas. Skapa inte en ny task för att ersätta en befintlig som tillfälligt inte kan läsas.

## Välj nästa genomförbara task

Återuppta först redan plockat eget arbete om det finns. Välj annars första Planned-task som är körbar enligt backloggens ordning och arkitekturplanens faser 1–12. F-01 är första kandidaten i den ursprungliga boarden; vid senare tillfällen avgör aktuell status vilket arbete som återstår.

Ett beroende inom epicen är uppfyllt efter granskad Task → Epic-merge och godkänd integration. En föregående epic ska vara verifierad och mergad till main. TeamPlayers 227 taskberoenden spärrar start efter föregående tasks, men de bevisar inte att föregående epic är integrerad i main.

Om en tidigare task är blockerad: dokumentera orsaken, berörda beroenden och varför en senare task kan köras självständigt. Uppdatera backloggens nästa steg och TeamPlayers berörda tasktext innan den ändrade ordningen används. Välj aldrig en senare task vars beroende fortfarande är ofärdigt.

## Läs underlagen och kontrollera förutsättningar

Läs hela TeamPlayer-tasken, motsvarande feature i backloggen, arbetsinstruktion, acceptans och relevanta A-/W-avsnitt. Kontrollera nödvändiga beslut, testdata, externa gränssnitt och förutsättningarna X-01–X-03 där de gäller.

En task som provar en ännu okänd adapter behöver åtkomst till miljön; den kräver inte att samma prov redan är genomfört. Beroende implementationer behöver däremot det verifierade kontraktet. Registrera konkret saknad förutsättning och nästa verifierbara åtgärd.

## Förbered rätt epic och task worktree

Epicens integrationsbranch är `feature/epic-<epic-id>` från aktuell main, exempelvis `feature/epic-e01`. Varje feature/task har `task/<epic-id>-<task-id>`, exempelvis `task/e01-f01`, från aktuell epic-branch. Branches använder dokumenterad ID-koppling och varje aktiv branch har eget worktree.

Alla epicens features integreras i dess epic-branch. Implementera respektive feature i task-worktreet, med Worker-rollens begränsningar. Integration-rollen ansvarar för taskreview och Task → Epic-merge; Coordinator ansvarar för slutreview och Epic → main-merge. Ange branch, worktree, bas-SHA och aktuell roll innan arbetet börjar.

Under bootstrap utför ansvarig utvecklare rollerna sekventiellt. En rollövergång ska vara tydlig i review- och mergeunderlaget. När Git Manager och andra services är implementerade används dessa för kritiska operationer. Bevara befintliga lokala ändringar och kontrollera rent relevant arbetsläge före synk och merge.

Kod från annan epic hämtas normalt efter dess main-merge. Avsteg kräver dokumenterat integrationsbeslut och uppdaterade beroenden. Hantera konflikter i rätt worktree och verifiera ändrat underlag igen.

## Plocka tasken före kodändring

Hämta `get_task(projectId, taskId)` och kontrollera aktuell version, tilldelning, status och beroenden. För en ny task gör Integration-rollen/bootstrapansvarig följande:

1. Anropa `update_task_status` med `projectId`, `taskId`, `version`, `status=InProgress` och konkret `statusReason` som anger feature, epic och startat arbete.
2. Kontrollera mutationssvaret; vid okänt resultat återläs tasken.
3. Uppdatera featurestatus och Kanbanrad till `Active` i backloggen samt ange branch och nästa steg.

Coordinator kontrollerar samtidigt att taskens påbörjade epic är Active i TeamPlayer och backloggen. Vid första tasken ändras epicens Pending till InProgress med dess egen färska version.

Vid versionskonflikt hämtas tasken på nytt och den nya statusen/tilldelningen bedöms före nytt försök. En redan Active-task ska återupptas med sitt kända worktree och underlag, inte claima en ny körning. Bekräftad Worker-start gäller när arbetet körs genom orchestratorn.

En särskild begäran om dokumentation eller AGENTS.md ändrar inte automatiskt en implementationsfeatures status. Tasken plockas när det verkliga arbetet med dess scope börjar.

## Implementera hela featuren

Bygg det konkreta resultatet inom angivet scope. Bevara kontrakt, runtime-identiteter, idempotens, rollgränser och återstartsregler för de delar som påverkas. Lägg inte till orelaterad omstrukturering.

Om scope, acceptans eller ett styrande kontrakt behöver ändras: uppdatera först backloggen och rätt källdokument. Uppdatera därefter TeamPlayers taskbeskrivning och särskilda acceptansfält via `update_task_details` med färsk version och motivering. Kontrollera att dokument och task beskriver samma uppdrag innan implementationen fortsätter.

## Verifiera resultatet

Kör taskens relevanta tester och prov. Kontrollera tillstånd, persistens, datakorrekta resultat, rollkontroll, Git/worktree-isolering, samtidighet och berörd transport eller operatörsvy där dessa ingår. Tvinga inte fram UI- eller databasprov för en task som inte påverkar dem.

Tasken kan sättas till `Testing`, som ligger i Active. Löpande taskreview och korrigering behåller `InProgress` eller `Testing`. `NeedsReview` används när ett faktiskt granskningsbeslut utifrån inväntas och ligger i Attention.

Dokumentera kommandon, exitkoder, testmiljö, kända begränsningar och vilka kriterier som uppfylls på vilken commit. Simulerade adaptrar och verkliga externa integrationer redovisas separat. Tester utan underlag får inte räknas som genomförda.

## Hantera verkliga hinder

Välj precis API-status för hindret och spegla dess Kanbankolumn i backloggen:

| API-status | Kanban | När den används |
| --- | --- | --- |
| Pending | Planned | Ej startat arbete, även väntan på känt beroende. |
| InProgress, Testing, Paused | Active | Arbete, verifiering eller avsiktlig kort paus. |
| Blocked | Attention | Tekniskt eller beroenderelaterat hinder i påbörjat arbete. |
| NeedsInput | Attention | Uppgift, testdata eller svar saknas. |
| NeedsApproval, NeedsBudgetApproval | Attention | Ett faktiskt beslut eller budgetbeslut behövs. |
| NeedsReview | Attention | Extern granskning inväntas. |
| Failed | Attention | Körningen har misslyckats; återställningsåtgärd krävs. |
| Done | Done | Verifierad och integrerad task. |

`Active` motsvarar Pågår, `Attention` dokumenterat åtgärdsbehov/Blockerad och `Done` Klar. Behåll dessa etablerade Kanban-värden i backloggen. Cancelled redovisas som avgränsning och är inte verifierad acceptans även om TeamPlayer placerar den i Done-kolumnen.

Ange orsak, vad som provats, behövd input, ansvarig roll, nästa åtgärd och eventuell ändrad ordning. Skriv detta i backloggen, TeamPlayers taskbeskrivning och statusReason med aktuell version. Om ett kommentarverktyg saknas används taskbeskrivningen för beständig historik.

När produktens Worker-parkering används: behåll session/branch/worktree, bekräfta inaktivitet före slotrelease och återuppta samma session när input och kapacitet finns. En misslyckad parkering eller okänt stoppresultat bevisar inte att sloten är fri.

## Avsluta den verifierade featuren

Worker committar ändringarna och lämnar READY_FOR_REVIEW med task-ID, branch, commit, tester, sammanfattning och begränsningar. Tasken är fortfarande Active.

Integration-rollen synkroniserar mot aktuell epic, kör berörda kontroller och granskar taskens diff, acceptans och underlag. Godkännande gäller exakta task/epic-SHA. Vid ändrad kod eller bas krävs förnyad relevant verifiering. Korrigering går tillbaka till samma Worker-session när sådan används.

Efter godkänd review integrerar Integration-rollen tasken till epic-branchen med `--no-ff` och kör relevanta integrationstester. Först när detta lyckats sätts tasken till `Done` med aktuell TeamPlayer-version och merge-/verifieringsreferenser i statusReason.

Uppdatera featurebeskrivningen och Kanbanraden till Done, markera verifierade kriterier och registrera taskcommit, reviewunderlag, taskens merge-SHA, testresultat och begränsningar. Uppdatera epicens eget verifieringsantal endast för kriterier som faktiskt bevisats. En task kan vara Done medan epicen fortfarande är Active och väntar på övriga leveranser eller main-integration.

## Stäm av och välj nästa task

Återläs tasken och kontrollera att TeamPlayer och backloggen visar samma status, tilldelning, acceptans och beroenden. Bevara kända lokala delresultat om extern synk misslyckas och återförsök endast den skrivning som saknas.

Välj nästa genomförbara feature när användaruppdraget omfattar fortsatt backloggarbete. Om uppdraget gäller en särskild feature avslutas den leveransen med verifiering och aktuell status. Starta ingen andra implementationsfeature parallellt.

## Integrera hela epicen

När samtliga ingående tasks är Done gör Integration-rollen samlad build, test och epicacceptans och lämnar EPIC_READY_FOR_REVIEW. Coordinator öppnar en PR mot main och granskar diff, integration mellan tasks, kontrakt, schema/migrationer, rollkontroll, recovery och dokumentation.

Synkronisera mot aktuell main och verifiera på nytt där bas eller konflikter ändrar underlaget. Vid EPIC_CHANGES_REQUESTED genomförs kopplad korrigering med nya aktuella reviews. När slutreview är godkänd integrerar Coordinator epicen med `--no-ff` och kör slutverifiering på main.

Efter slutverifiering skriver Coordinator epicen Done i TeamPlayer med `update_epic_status` och aktuell epicversion. Återläs och uppdatera epicbeskrivning samt Kanbanrad i backloggen med PR, main-mergecommit och slutverifiering. Nästa beroende epic utgår från uppdaterad main. Om merge har utförts men tester återstår bevaras merge-SHA och epicen behåller Active. Om endast extern synk återstår dokumenteras leveransen och väntande synk; upprepa inte merge och rapportera inte källorna som synkroniserade. Saknad GitHub-åtkomst eller remote håller epicen Active med separat blockerarrapport.

## Instruktioner per katalog

Gemensamma regler finns i [AGENTS.md](AGENTS.md). [src/AGENTS.md](src/AGENTS.md) preciserar implementation, [tests/AGENTS.md](tests/AGENTS.md) verifiering och [prompts/AGENTS.md](prompts/AGENTS.md) rollprompts. Läs de underordnade instruktionerna även när sessionen startats i repo-roten. Codex läser instruktioner längs katalogvägen vid start enligt [OpenAI Docs om AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md).
