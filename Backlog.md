# HerdrCoordinator komplett backlogg

HerdrCoordinator ska genomföra utvecklingsarbete från TeamPlayer till verifierad merge i `main` genom Codex-agenter i Herdr. Denna backlogg omfattar hela implementationsplanen: **12 epics och 50 tasks**, från grundplattform till recovery och härdad drift. Varje task är en avgränsad feature och ett möjligt Worker-uppdrag.

**Statuskälla:** TeamPlayer HerdrCoordinator, avstämd 2026-10-08. E-01–E-05 och F-01–F-22 Done. PR #3/main 0395728 slutverifierad med 262 tester; E-04 Done; PR #4/main56c5aff slutverifierad med365tester. E-05 Done; PR#5/main9d16c7f slutverifierad. E06 Active/F23–F24 Done; F25 pågår; E07–E12 och återstående tasks Planned. E-02 PR #2/main 9beaf34 verifierad med 187 sluttester.

## Styrande underlag

| Referens | Dokument | Styr |
| --- | --- | --- |
| G | [Epic och Feature Guide](Epic%26Feature%20Guide.md) | Mallar, prioritet, körbarhet och krav på verifierbar leverans. |
| A | [Arkitektur och implementationsplan](Arkitektur%20och%20implementationsplan%20%E2%80%93%20Herdr%20-Codex%20Multi-Agent%20Workflow.md) | Komponenter, tekniska kontrakt, runtime och faser 1–12. |
| W | [Herdr Workflow](Herdr_Workflow.md) | Roller, worktrees, review, merge och Done. |

Hänvisningar som `A §35` och `W §25` avser numrerade avsnitt i dessa dokument. W styr ansvar och leveransregler; A styr teknik och fasordning. Backloggens kontraktsdetaljer är planeringsbeslut som konkretiserar underlagen och ska fastställas i angiven task. Externa API-namn fastställs först vid adapterverifiering.

## Mål omfattning och leveransgrindar

MVP gäller ett repository, en aktiv epic och maximalt två aktiva Workers. Coordinator äger projektet och `main`, Integration Agent äger en epic och Worker en task i eget worktree. Orchestratorn validerar kritiska operationer; Git, Herdr/Codex och TeamPlayer nås genom adaptrar. SQLite bevarar runtime medan TeamPlayer är primär källa för arbetsstatus när kopplingen finns.

| Grind | Efter epic | Resultat som måste visas |
| --- | --- | --- |
| Grundplattform | E-01 | Tjänstestart, beständig state och rollkontroll utan AI-automation. |
| Git och runtime | E-03 | Isolerade worktrees och verifierade verkliga Herdr/Codex-operationer. |
| Worker MVP | E-04 | En task till commit och READY_FOR_REVIEW, utan automatisk merge. |
| Taskintegration | E-05 | Review, korrigering och verifierad Task → Epic-merge. |
| Kanban och parallellitet | E-08 | TeamPlayer, två Workers, beroenden, Attention och samma-session-resume. |
| Första autonoma version | E-10 | Integration Agent och Coordinator driver hela epicflödet till main och nästa epic. |
| Robust drift | E-12 | Recovery, samtidighetsskydd, felhantering, audit och verifierad återställning. |

Flera samtidiga epics, fler än två Workers, tasks över flera repositories, dynamisk scaling och automatisk konfliktlösning på epicnivå ligger utanför denna leverans. De ska få en separat backlogg om omfattningen senare utökas.

## Identiteter status och körbarhet

`E-01`–`E-12` är lokala epic-ID:n. `F-01`–`F-50` är lokala feature/task-ID:n med en task per feature. Varje lokal identitet är kopplad till det registrerade TeamPlayer-ID:t i sin beskrivning och Kanbanöversikten. Varje runtime-run får eget ID och varje ny körning eller korrigering behåller länken till tidigare historik.

**Backloggtasks bygger HerdrCoordinator.** Tasks och epics som körs genom systemet i integrationsproven har separata test-ID:n. Exempelvis får produktprovet i E-04 stanna i READY_FOR_REVIEW medan implementationstasken F-17 kan integreras och bli Done genom bootstrap-processen.

| Kanban | Betydelse |
| --- | --- |
| Planned | Planerad leverans; kan invänta beroende eller extern förutsättning. |
| Active | Bekräftat arbete eller review/fix/merge. |
| Attention | Extern input eller åtgärd krävs; orsak och nästa åtgärd sparas. |
| Done | Task: godkänd review, merge till epic och passerad integration. Epic: slutreview, merge till main och passerad slutverifiering. |

**Epics använder endast Planned/Active/Done.** Planned betyder ej påbörjad; Active sätts av Coordinator vid epicstart och senast när första tasken plockas. Påbörjade epics behåller Active genom implementation, review, korrigering, paus, blockerare och väntan på PR/main-integration/slutverifiering. Hinder dokumenteras separat och ger inte epicstatus Attention. Alla tasks Done räcker inte för epic-Done. Done kräver epicens fulla definition av Done nedan. Återöppning för nytt arbete dokumenteras och ger Active. Tabellen ovan används med samtliga fyra värden för tasks.

Coordinator läser epicens status/version med `list_epics` och skriver `update_epic_status(projectId, epicId, version, status)` med färsk version. API-status Pending mappar till Planned, InProgress till Active och Done till Done. Återläs och håll Kanbanrad överens med statusfältet. Synka epicbeskrivningen via en verifierad redigeringsväg; saknad sådan dokumenteras som separat textavvikelse. Vid versionskonflikt/okänt nätutfall återläs före retry; väntande synk dokumenteras och Git-merge upprepas inte. Interna EpicRun-faser mellan PLANNED och verifierad DONE visas som Active på boarden.

Körbarhet är separat från status. En task blir körbar när angivna beroenden är verifierade, krav och testförutsättningar finns och externa villkor är uppfyllda. Taskberoende inom samma epic kräver granskad integration i epic-branchen. Beroende på tidigare epic kräver dess verifierade merge till `main`. Varje epic efter E-01 kräver föregående epic Done; detta gäller samtliga tasks i epicen utöver deras uttryckliga taskberoenden.

Interna tasktillstånd följer A §§15–16: `PLANNED`, `CLAIMED`, `STARTING` motsvarar Planned; `WORKING`, `READY_FOR_REVIEW`, `REVIEWING`, `CHANGES_REQUESTED`, `APPROVED`, `MERGING` motsvarar Active; `BLOCKED`, `PARKED` motsvarar Attention; `DONE` motsvarar Done. W:s `ASSIGNING` är inget ytterligare Kanban-tillstånd.

## Gemensamma kontrakt och leveransregler

Dessa regler gäller varje epic och task och ska användas tillsammans med taskens egna kontrakt.

1. **Identitet och roll:** knyt varje operation till betrodd aktör, project/epic/task/run. Worker får rapportera egen task; Integration får styra egen epic; Coordinator får initiera epicintegration till main. Verktygsargument eller prompttext ger inte behörighet.
2. **Git och isolering:** epicbranch är `feature/epic-<epic-id>` och taskbranch `task/<epic-id>-<task-id>`. Varje aktiv branch har eget worktree under konfigurerad rot. Validera branch, path, ägarskap, rent arbetsläge och commits före kritiska operationer. Worktreeexemplen i källorna är inte verkliga paths för detta projekt.
3. **Persistens och sidoeffekter:** använd beständiga runs och operationsreferenser för start, review, merge, park/resume, synk och cleanup. Spara skapade resurser och kända resultat innan fortsatt automation. Ett agentpåstående eller Kanbanstatus ersätter inte Git-/runtimebevis.
4. **Review och merge:** review binds till task/epic-SHA respektive epic/main-SHA och aktuellt testunderlag. Ny kod eller bas kräver ny relevant verifiering. Leveransmerge görs med `--no-ff` genom Git Manager på rätt rolls begäran. Integration Agent synkroniserar epic → task genom separat Git-operation; Worker mergear aldrig.
5. **Slots och Attention:** reservera kapacitet före start och frigör först vid bekräftad parkering eller avslut. Max två aktiva Workers, med en Worker under E-04. BLOCKED/Attention utan bekräftad parkering bevisar inte att kapacitet är fri. Resume behåller session, branch och worktree och kräver ledig slot.
6. **Fel och verifiering:** ett fel efter utförd merge behåller faktisk merge-SHA och ej-Done-läge tills verifiering är klar. Nätfel efter lokal framgång återförsöker bara synk. Hemligheter hålls utanför repository, loggar och agentkontext. Tester använder temporära repositories eller testepics; simulerad adapter och verklig integration redovisas separat.

### Definition av Done för varje task

- [ ] Taskens numrerade acceptanskriterier är verifierade med sparat underlag.
- [ ] Relevant kod, schema, prompts och dokumentation är committade i taskbranch.
- [ ] Tasken är synkroniserad mot aktuell epic och har godkänd review för rätt commits.
- [ ] Tasken är mergad till epic-branchen och relevanta integrationstester passerar.
- [ ] Review-, test- och mergeunderlag är registrerade; statuskällan och Kanbanöversikten är uppdaterade.

### Definition av Done för varje epic

- [ ] Alla ingående tasks är Done och epicens numrerade acceptans är verifierad.
- [ ] Samlad build, tester och epicacceptans passerar på aktuella commits.
- [ ] Coordinator har öppnat epicens PR och genomfört samt sparat slutreview mot aktuell main.
- [ ] Epicen är mergad till main och slutverifieringen passerar.
- [ ] PR, merge-SHA och verifiering är registrerade; statuskällan och Kanbanöversikten är uppdaterade.

## Bootstrap och praktisk arbetsgång

Utvecklingen av denna backlogg följer [Utvecklingsprocess.md](Utvecklingsprocess.md) och [AGENTS.md](AGENTS.md): en feature/task i taget, plockad i TeamPlayer före kodändring. Epics använder Planned/Active/Done och tasks Planned/Active/Attention/Done; registrera faktisk API-status vid behov. Varje epic slutgranskas via PR mot main. Produktens tester får använda två Workers enligt acceptansen utan att implementationsfeatures utvecklas parallellt.

Före full orkestrering utför ansvarig utvecklare Coordinator- och Integration-rollerna manuellt. Skapa epicbranch från aktuell main och taskbranches från epicen, arbeta i separata worktrees, granska och kör relevanta tester före merge. Registrera commits, review och testunderlag lokalt. Epicens byggda produktfunktion och arbetsprocessen för att bygga den behöver inte ha samma automationsgrad.

E-01 etablerar tjänsten, E-02 Git-automation, E-03 runtime, E-04 en Worker och E-05 taskreview. E-06 ansluter TeamPlayer. E-07 har servicebaserad scheduling; E-09 flyttar beslut till en långlivad Integration Agent. E-10 inför Coordinator-loopen. Före dessa leveranser används endast redan verifierade verktyg och dokumenterad manuell motsvarighet.

Backloggen är upplagd i TeamPlayer och epic/taskkopplingar, tilldelning, beskrivningar, prioriteringar, acceptans och taskberoenden har återlästs och verifierats. Dokumentöversikten synkas efter varje leverans. Uppläggningen ändrar inte de fortfarande overifierade implementationskriterierna.

## TeamPlayer koppling

**Projekt:** HerdrCoordinator. **Projekt-ID:** `d2ee4c75-7b80-465f-83ac-1750854a8e80`. **Utförare:** `blitterbot@gmail.com`. **Verifierat användar-ID:** `105f26a7-0648-438d-94fd-3260ac3af4ee`.

Samtliga 50 tasks har `executionOwnerKind=User` och det verifierade kontot som utförare. Skapandets valideringsansvar är också satt till detta användarkonto. Detta är administrativ tilldelning; kodgranskning och integration följer fortfarande agentrollerna i arbetsprocessen. TeamPlayers board epics har inget tilldelningsfält; kontot anges som ansvarigt i varje epicbeskrivning. De har ett verifierat status-/versionsfält och statusverktyget update_epic_status. Coordinator synkar epicstatus med denna backlogg utifrån faktiskt arbete, Git-/review-/testunderlag och senare EpicRun.

**Avstämning av epicstatus och beskrivningar (2026-10-07):** TeamPlayers statusfält är återlästa: E-01 Done, E-02 InProgress/Active och E-03–E-12 Pending/Planned. Efter MCP-uppdateringen har alla 12 epicbeskrivningar synkats via `update_epic_details(projectId, epicId, version, description)` med färsk version och exakt återläst text. Beskrivningarna har rätt epic-ID, status, scope, beroenden, acceptans och regler för Planned/Active/Done. E-01 har verifierad PR/main-leverans och tre uppfyllda kriterier; E-02 har F-05:s integrationsunderlag och ett uppfyllt kriterium. Det tidigare hindret med saknat redigeringsverktyg/HTTP 403 vid REST-läsning är löst genom MCP och kräver ingen REST-skrivning. Epicstatusarna är oförändrade och ingen implementationstask har startats.

Tasktypen är `Task`. Prioritet mappas `P0 → Critical`, `P1 → High`, `P2 → Medium`, `P3 → Low`. Kanbankolumnen Planned representeras av API-status Pending. Använd det verifierade API-kontraktet vid kommande statusändringar.

Varje task har en sammanhängande Codex-beskrivning med syfte, mål, scope, konkreta arbetssteg, förväntat beteende, tekniska krav, beroenden, källfiler, acceptans, testinstruktioner och leveransregler. De 150 taskkriterierna finns dessutom i TeamPlayers särskilda acceptansfält; epicernas 36 kriterier finns i epicbeskrivningarna.

Totalt 227 taskberoenden är registrerade. Ett beroende på en föregående epic representeras i TeamPlayer av beroenden till samtliga dess tasks. Detta är en startspärr som kompletteras med kravet på verifierad epicmerge till main i varje taskbeskrivning; task-Done ensam bevisar inte den main-mergen.

## Planeringsbeslut och externa förutsättningar

| ID | Beslut eller förutsättning | Leverans som fastställer eller verifierar |
| --- | --- | --- |
| D-01 | F-01 fastställer Python 3.12+, asyncio, Pydantic 2 och sqlite3. src/orchestrator, TOML, Hatchling, pytest och Ruff; versionslås i uv.lock och verifieringsmiljö Python 3.13. | Beslut dokumenterat i README; persistens levereras i F-02. |
| D-02 | F-04: en stdio-process per betrodd aktör; operatören/MCP-värden styr startkommando och extern skyddad JSON-profil med roll/project/epicrun/taskrun. Verktygsargument ger ingen behörighet. Worker får inte skriva profil, startkonfiguration eller databas. Filrättigheter isolerar inte samma OS-användare; runtime/shellgränser verifieras separat. | Lokal MCP-behörighet verifierad i F-04. Verklig sandboxisolering återstår i F-10/F-17; olösta nödvändiga gränser blockerar F-38. |
| D-03 | Slots reserveras från CLAIMED/STARTING och hålls genom review/fix tills bekräftad parkering eller avslut. Kanban Active är inte sloträknare. | F-15, F-28 och F-31–F-32. |
| D-04 | F-14: JSON schema/prompt/rapport version 1. READY_FOR_REVIEW/BLOCKED skiljs från E-03 WORKING-ACK. Legacy TASK/TASK_ID kräver verifierad sessions-/runidentitet; Git/testpåståenden verifieras i F-16. | F-14 och F-16. |
| D-05 | Epic → Task är en separat synkoperation enligt W §23. Leveransmerge går Task → Epic → main; synk utförs av Git Manager på Integration-rollens begäran. | F-07 och F-18. |
| D-06 | Run-, operations-, väntande synk- och epicreviewhistorik konkretiserar källornas runtime/recoverykrav. Backup/restore i F-50 kompletterar recovery med ett verifierbart driftprov. | F-02, F-25, F-39 och F-42–F-50. |
| X-01 | Installerad och åtkomlig Herdr/Codex-runtime och ett ofarligt testrepository. Start/resume/park/sandboxkapabiliteter fastställs i respektive verifieringstask; de krävs inte som förkunskap för F-10. | F-10–F-13. Credentials förvaras externt. |
| X-02 | Tillgänglig TeamPlayer MCP, projekt och särskild testepic med avsedd behörighet. ID:n, läs/skrivkontrakt, create/reopen och kommentaravstämning fastställs i verifieringstaskerna. | F-23–F-26 och F-39. |
| X-03 | Isolerad drift/testmaskin för process-, Herdr-, SSH- och maskinavbrott samt återställning. Pi används bara om den är vald målmiljö. | F-45 och F-50. |

Ett beroende vars externa förutsättning saknas behåller Planned tills start är möjlig. Om påbörjad automation behöver extern åtgärd används Attention med konkret orsak. Ingen ej verifierad API-förmåga eller miljö räknas som tillgänglig.

## Epicöversikt och leveransordning

| Ordning | Epic | Fas | Prioritet | Resultat | Beroende | Tasks |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | E-01 | 1 | P0 | Starta orchestratorn och bevara körningarnas tillstånd | Inget | F-01–F-04 |
| 2 | E-02 | 2 | P0 | Isolera och integrera arbete genom Git worktrees | E-01 Done på main | F-05–F-09 |
| 3 | E-03 | 3 | P1 | Starta och återanslut agentruntime genom Herdr | E-02 Done på main | F-10–F-13 |
| 4 | E-04 | 4 | P1 | Låt en Worker leverera en verifierbar task | E-03 Done på main | F-14–F-17 |
| 5 | E-05 | 5 | P1 | Granska korrigera och integrera en task | E-04 Done på main | F-18–F-22 |
| 6 | E-06 | 6 | P1 | Spegla arbetsflödet i TeamPlayer | E-05 Done på main | F-23–F-26 |
| 7 | E-07 | 7 | P1 | Genomför beroendestyrda tasks med två Workers | E-06 Done på main | F-27–F-30 |
| 8 | E-08 | 8 | P1 | Parkera blockerade tasks och återuppta samma arbete | E-07 Done på main | F-31–F-33 |
| 9 | E-09 | 9 | P1 | Låt en långlivad Integration Agent driva en epic | E-08 Done på main | F-34–F-37 |
| 10 | E-10 | 10 | P1 | Slutgranska integrera och välj nästa epic med Coordinator | E-09 Done på main | F-38–F-41 |
| 11 | E-11 | 11 | P1 | Återhämta körningar efter avbrott och omstart | E-10 Done på main | F-42–F-45 |
| 12 | E-12 | 12 | P0 | Härda orchestrering och gör drift spårbar | E-11 Done på main | F-46–F-50 |

Ordningen mellan epics är sekventiell enligt faserna och implementationen sker en task i taget. Oberoende tasks kan väljas efter en dokumenterad blockerare utan att kringgå beroenden. Parallella Workers förekommer i produktens verifieringsscenarier enligt den berörda featurens acceptans.

## Epic E-01 Starta orchestratorn och bevara körningarnas tillstånd

**Fas:** 1. **Prioritet:** P0. **Kanban-status:** Done. **TeamPlayer Epic-ID:** `0da5c7c4-6e29-475e-aeb6-ce887f3864db`.

**Körbar:** Levererad — PR #1, main-merge och slutverifiering är genomförda. **Beroende:** Inget.

**Källa:** A §§4.4, 11–16, 31–35, 49–50; W §§4, 13–15, 35, 39–40.

### Resultat och omfattning

Operatören kan starta en lokal tjänst, konfigurera ett projekt och bevara epic-, task- och reviewinformation utan AI-automation.

**Ingår:** Python-projekt, konfiguration, SQLite, domänmodeller, övergångsregler, loggning och ett lokalt MCP-skal med rollkontroll. **Utanför:** Git-sidoeffekter, Herdr-sessioner och externa TeamPlayer-anrop.

**Gemensamma regler:** tillämpa samtliga sex kontrakt ovan och de ingående taskernas förvillkor. **Risk och beslutspunkt:** D-01 och D-02 avgör lagringsbas och hur ett anrop knyts till en betrodd roll; besluten levereras i F-01 respektive F-04.

### Tasks och beroenden

| Task | Leverans | Taskberoenden utöver epicens beroende | Prioritet |
| --- | --- | --- | --- |
| F-01 | Starta ett konfigurerbart Python-projekt | Inget | P0 |
| F-02 | Spara runs och reviewhistorik i SQLite | F-01 | P0 |
| F-03 | Validera task och epic genom explicita tillstånd | F-02 | P0 |
| F-04 | Exponera lokala MCP-kontrakt med betrodda roller | F-03 | P0 |

### Epicacceptans

- [x] **E-01.A1:** Tjänsten startar från dokumenterad konfiguration och en andra start bevarar tidigare runs.
- [x] **E-01.A2:** EpicRun, TaskRun och Review kan läsas efter omstart med samma relationer och identiteter.
- [x] **E-01.A3:** Ogiltiga övergångar och otillåtna rollanrop avvisas utan ändrad state.

Epicen följer dessutom den gemensamma definitionen av Done. Acceptansen verifieras genom taskernas underlag och ett samlat prov av epicens resultat.

**Samlad verifiering E-01 (2026-10-07):** Epiccommit `d37fcfdffbac09677ab4a68df16302a770fcec16` mot main `7d1f832fee14ad43c7bec6e618c06da0684fbc78`. 64 pytesttester, Ruff, diffkontroll, wheel/sdist-build och CLI --check passerar. A1/A2: två riktiga MCP-serverstarter mot samma SQLite-fil och återläsning av epic/task/reviews. A3: otillåtna stateövergångar, stale approval, roll/scope och rollinjektion avvisas utan stateändring. E-01 blir Done först efter PR, main-merge och slutverifiering.

**Integrationshinder (2026-10-07):** GitHub-push av både main och epic samt main-retry med HTTP/1.1 avvisas av servern. Återläst fjärrläge är tomt trots verifierad push/admin-åtkomst. Lokal kod, commits och 64 passerade tester är bevarade. Coordinator ska återläsa fjärrläget, publicera branches, öppna den förberedda PR:n och genomföra aktuell review/main-merge/slutverifiering. Se [fullt granskningsunderlag och förberedd PR](docs/reviews/E-01.md). F-05 inväntar denna leveransgrind.

**Återupptagen integration (2026-10-07):** Fjärrläget återlästes som tomt före återförsök. Push av main och feature/epic-e01 lyckades utan force. Det tidigare serverfelet är löst; Coordinator öppnar PR och granskar/verifierar aktuell leverans mot main. F-05 inväntar fortfarande epicens main-grind.

**Slutleverans E-01 (2026-10-07):** [PR #1](https://github.com/johanLstang/HerdrCoordinator/pull/1), mergecommit `9cafb75c9136614b1c8ddf4c740fa7368c971d66`. Bootstrap Coordinator-review godkänd för epic `db8557db05207efc9f38ab729f987929e2d9c51a` mot main `7d1f832fee14ad43c7bec6e618c06da0684fbc78`. PR var mergeable/clean utan externa CI-checkar. Merge har två parents och bevarar task→epic-historiken. Slutverifiering på faktisk main: 64 pytesttester, Ruff, wheel/sdist-build och CLI --check passerar, exit 0. E-01.A1–A3 verifierade; publiceringshindret är löst.

### Task F-01 Starta ett konfigurerbart Python-projekt

**Epic/fas/prioritet:** E-01 / 1 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `9bc95f85-f05d-4842-b517-c1f8132c49ab`.

**Körbar:** Levererad — task/e01-f01 är granskad och integrerad i feature/epic-e01.

**Källa:** A §§4.4, 34–35, 49; W §§1–4. **Berör:** Projektstruktur, konfiguration, startkommando, loggning.

**Beroenden:** Inget. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Skapa minsta Python-paket, pyproject.toml, testkonfiguration och dokumenterad lokal start. Definiera validerad konfiguration för repository, worktree-rot, SQLite-path och workergräns. Dokumentera D-01 och versionsval; starta inga agenter.

**Resultat och kontrakt:** Konfiguration läses och valideras innan tjänsten gör sidoeffekter. Föreslagen bas är Python 3.12+, asyncio, Pydantic och sqlite3; F-01 ska fastställa valet. Loggar har operation, nivå och korrelations-ID. Hemligheter tas från extern konfiguration och maskeras.

**Acceptans**

- [x] **F-01.A1:** Givet giltiga paths startar tjänsten och rapporterar vald konfiguration utan credentials.
- [x] **F-01.A2:** Givet ogiltig workergräns, saknat repository eller otillåten worktree-rot avslutas start med begripligt fel innan externa operationer.
- [x] **F-01.A3:** Ett loggat konfigurationsfel exponerar inte en testhemlighet.

**Verifiering:** Lokal start i temporär konfiguration, felkonfigurationer och loggkontroll. Dokumentera installations- och startkommandon.

**Verifieringsunderlag F-01 (2026-10-07):** Python 3.13.14. `uv run --locked pytest`: 19 passerade, exit 0 (A1–A3). `uv run --locked ruff check .`: exit 0. `uv build`: wheel och sdist, exit 0. `uv run --locked herdr-coordinator --config herdr.example.toml --check`: exit 0. CLI-prov täcker riktiga temporära Git-repositories, ogiltiga workergränser/paths, symlänk till metadata, planterad hemlighet och SIGTERM. Inga externa agent- eller TeamPlayer-adaptrar körs. Bootstrap Integration-review godkänd för task `af84f4df958161649108a6f25a50252a97a604ad` mot epicbas `7d1f832fee14ad43c7bec6e618c06da0684fbc78`. Merge `7dc5d80e5e36c01f8b639fe0eb765b37580e600a` med --no-ff; 19 tester passerade efter merge, exit 0. TeamPlayer Done återläst. Epicen väntar på F-02–F-04 och main-integration.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-02 Spara runs och reviewhistorik i SQLite

**Epic/fas/prioritet:** E-01 / 1 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `b555e011-5e55-4cae-8d14-9cdd57725e5c`.

**Körbar:** Levererad — integrerad i E-01 via 8e29ef2.

**Källa:** A §§11–14, 35; W §§19–20, 35. **Berör:** Domänmodeller, SQLite, schemaversion.

**Beroenden:** F-01. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera EpicRun, TaskRun och Review med fälten i arkitekturplanen, repositories och första schemaversionen. Lägg till beständiga operationer och externa referenser som senare start-, merge- och synkflöden behöver; definiera unika nycklar och UTC-tider.

**Resultat och kontrakt:** TaskRun hör till en EpicRun och Review till en TaskRun. Spara projekt/task-ID, branch, worktree, bas/aktuell commit, slot och runtime-ID; ännu okända externa ID:n får vara null. En aktiv task får inte ha två ägande runs. Schema initieras idempotent och relationer valideras.

**Acceptans**

- [x] **F-02.A1:** En sparad epic, task och två reviews läses tillbaka efter att databasanslutningen stängts och öppnats.
- [x] **F-02.A2:** Task utan giltig epic och dubbla aktiva runs för samma projekt/task avvisas utan partiellt sparade rader.
- [x] **F-02.A3:** Andra schema-initieringen behåller data; en okänd framtida schemaversion stoppas med tydligt fel.

**Verifiering:** Temporär SQLite-fil, relations- och transaktionstest samt återöppning med oberoende förväntade fält.

**Verifieringsunderlag F-02 (2026-10-07):** Python 3.13.14. `uv run --locked pytest`: 28 passerade, exit 0; `uv run --locked ruff check .` och `git diff --check`: exit 0. Provar återöppnad fil, två reviews, beständiga operationer/referenser, två separata anslutningar, saknad/felprojekterad parent, rollback efter injicerat fel, schemaomstart och framtida schema inklusive CLI-exit 3. Bootstrap Integration-review godkänd för task `0af2075da10e2bb949bc322bed247326245e4466` mot epic `3130e4e7cdfc62043f459c32760ed9a4622ccb09`. --no-ff-merge `8e29ef2c0ce052d63a70c327a121a4c60ba687bf`; 28 tester passerade efter merge. TeamPlayer Done återläst.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-03 Validera task och epic genom explicita tillstånd

**Epic/fas/prioritet:** E-01 / 1 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `6ca76316-bff1-40e6-b57d-dd6407e449dd`.

**Körbar:** Levererad — integrerad i E-01 via 904a022.

**Källa:** A §§15–16, 24–27, 35; W §§13–15, 19–25, 35–37, 40. **Berör:** Domän, övergångsregler, persistens.

**Beroenden:** F-02. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera tasktillstånden och Kanban-mappningen samt en dokumenterad epic-livscykel. Knyt övergångar till förvillkor, aktör och beständigt event. Definiera återgång vid misslyckad start, review, merge eller verifiering utan att fabricera framgång.

**Resultat och kontrakt:** Task använder CLAIMED enligt planen och PARKED för bekräftad parkering. Epic skiljer aktiv körning, redo för review, ändringsbegäran, godkännande, merge och Done. DONE kräver verifierings- och mergeunderlag; epictillstånd är ett internt kontrakt som fastställs här.

**Acceptans**

- [x] **F-03.A1:** Alla dokumenterade tasktillstånd ger rätt av de fyra Kanban-statusarna.
- [x] **F-03.A2:** Direkt WORKING → DONE och epic-Done utan main-merge avvisas; befintlig state består.
- [x] **F-03.A3:** CHANGES_REQUESTED kan återgå till arbete och BLOCKED till PARKED; samma event återspelas utan dubbla övergångar.

**Verifiering:** Tabellstyrda övergångstest med både tillåtna och förbjudna händelser, beständig återläsning och felinjicerad transaktion.

**Verifieringsunderlag F-03 (2026-10-07):** 53 pytesttester passerade på Python 3.13.14. Ruff och diffkontroll passerar. Alla 12 tasktillstånd, review/fix, park/resume, exakta commitgrindar, ogiltig Done, fel roll/scope, eventkonflikt, replay efter omstart, schema 1→2 och rollback av state+event provas. Adapterfakta är avgränsade fixtures; verklig Git/runtime-verifiering levereras i senare epics. Bootstrap Integration-review godkänd för task `3c6ad089cab572e7b7df3ca61a6c81226666f4c8` mot epic `79329d02e602a23e7173bb0bb8a355f70632f816`. Merge `904a02273d97e4c2509291e7cd07c6b8ced70346` med --no-ff; 53 integrationstester passerade utan varningar. TeamPlayer Done återläst.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-04 Exponera lokala MCP-kontrakt med betrodda roller

**Epic/fas/prioritet:** E-01 / 1 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `96cc0f5f-e737-4a19-897f-2419f690b2e0`.

**Körbar:** Levererad — integrerad i E-01 via d37fcfd.

**Källa:** A §§2, 4–5, 31, 50; W §§4, 11–12, 39–40. **Berör:** MCP, applikationstjänster, policy, loggning.

**Beroenden:** F-03. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Skapa lokalt MCP-skal och validerade anrops/resultatmodeller. Bind varje anslutning till registrerad roll, projekt och tillåten run enligt D-02. Publicera läsbar runtime-status och policykontroll; registrera muterande verktyg först när deras service finns.

**Resultat och kontrakt:** En rollsträng från prompt eller verktygsargument ger ingen behörighet. Worker får endast rapportera egen task; Integration får styra egen epic; Coordinator får styra projektets epics. Okända eller ännu oimplementerade operationer svarar strukturerat utan sidoeffekter. Logga beslut utan hemligheter.

**Acceptans**

- [x] **F-04.A1:** En registrerad Worker kan läsa tillåten egen runtime men inte utge sig för att vara Coordinator genom ändrade argument.
- [x] **F-04.A2:** Fel projekt/task eller oregistrerad anslutning avvisas före adapteranrop och databasändring.
- [x] **F-04.A3:** MCP-servern startar lokalt; okända operationer och valideringsfel ger dokumenterade felkoder.

**Verifiering:** Lokalt MCP-prov och serviceprov med inspelande adapterstubbar som visar att avvisade anrop ger noll sidoeffekter.

**Verifieringsunderlag F-04 (2026-10-07):** Python 3.13.14 och MCP SDK 2.3.0. `uv run --locked pytest`: 64 passerade, exit 0; Ruff och diffkontroll: exit 0. 11 MCP-prov omfattar riktig stdio-server som startas två gånger mot samma SQLite-fil, bibehållna epic/task/reviews, egen Worker-läsning, rollinjektion, fel scope, oregistrerad anslutning, okända/oimplementerade operationer, skyddad profil och sanerade fel/loggar. Avvisade anrop lämnar databasens ändringsräknare oförändrad; oregistrerad anslutning når inte ens runtime-läsning. Inga externa adaptrar finns eller körs i denna task. Runtime-sandboxprovet återstår i F-10/F-17. Bootstrap Integration-review godkänd för task `950771fc62fa79b213e858109109fa1f0c656fab` mot epic `8df53b4797549d0fa9aa619f19c04260bb3030c1`. --no-ff-merge `d37fcfdffbac09677ab4a68df16302a770fcec16`; 64 integrationstester, Ruff, build och CLI-check passerar efter merge. TeamPlayer Done återläst.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

## Epic E-02 Isolera och integrera arbete genom Git worktrees

**Fas:** 2. **Prioritet:** P0. **Kanban-status:** Done. **TeamPlayer Epic-ID:** `225cca70-7f09-4313-a020-52c8b0b7b069`.

**Körbar:** Levererad — PR #2 mergad och main slutverifierad; TeamPlayer Done version 16 återläst. **Beroende:** E-01 Done på main.

**Källa:** A §§8–10, 27, 36; W §§3–4, 6, 10, 23–26, 33, 40.

### Resultat och omfattning

Operatören kan skapa en epic och två task-worktrees, granska ändringar och integrera dem i rätt riktning med kontrollerad cleanup.

**Ingår:** Git Manager, identitets- och pathkontroll, worktree-skapande, diff, synkronisering och båda mergeoperationerna. **Utanför:** Automatiskt agentgodkännande, scheduling och automatisk konfliktlösning på epicnivå.

**Gemensamma regler:** tillämpa samtliga sex kontrakt ovan och de ingående taskernas förvillkor. **Risk och beslutspunkt:** Git kan ha ändrats mellan kontroll och merge. F-07 och F-08 måste kontrollera aktuella commits i samma skyddade operation; D-05 skiljer synkronisering från leveransmerge.

### Tasks och beroenden

| Task | Leverans | Taskberoenden utöver epicens beroende | Prioritet |
| --- | --- | --- | --- |
| F-05 | Skapa och återfinn epic och task worktrees | E-01 | P0 |
| F-06 | Leverera diff och aktuella Git fakta för granskning | F-05 | P0 |
| F-07 | Synkronisera task mot epic och integrera granskad task | F-06 | P0 |
| F-08 | Integrera godkänd epic till aktuell main | F-07 | P0 |
| F-09 | Avsluta Git resurser efter verifierad leverans | F-07, F-08 | P0 |

### Epicacceptans

- [x] **E-02.A1:** En epic och två separata tasks skapas från förväntade bascommits i ett temporärt repository.
- [x] **E-02.A2:** Tasks synkroniseras, verifieras och integreras med --no-ff; en godkänd epic integreras till main.
- [x] **E-02.A3:** Fel branch, smutsigt worktree, konflikt och otillåten riktning stoppar operationen utan dataförlust.

Epicen följer dessutom den gemensamma definitionen av Done. Acceptansen verifieras genom taskernas underlag och ett samlat prov av epicens resultat.

**Coordinator-handoff E-02 (2026-10-08):** Alla fem tasks är Done och 187 tester/Ruff/build passerar på F-09:s faktiska epicmerge. Main `7257907520961de115ef91b775af64d5502cf1bb` synkad via `7abd4b00e47fa8b4e245344ac14594f01e56b404`; README-EOF-konflikt löst med F-08/F-09-dokumentation bevarad. Ingen kod/test-/låskonfiguration ändrades vid synk. Den dåvarande PR/slutgrinden är nu genomförd enligt slutleveransen nedan. Se [epicreview](docs/reviews/E-02.md).

**Slutleverans E-02 (2026-10-08):** Alla fem tasks Done, egen acceptans 3/3, Coordinator-review APPROVED för epic `4c932a6d3ae0ae7602e385d326f2838918635445` mot main `7257907520961de115ef91b775af64d5502cf1bb`. Komplett diff 192536 bytes, SHA256 `6ed23635c19ade6a9046e6a64cc7896842b7bb0374d58482cdcf1b9a3320e3e1`. [PR #2](https://github.com/johanLstang/HerdrCoordinator/pull/2) mergad med två parents i `9beaf34ef4c77abc769ac5d0e9a201942ac95f1c`; exakt granskade parents och identisk godkänd tree återlästa. På faktisk mainmerge: 187 pytesttester, Ruff, diffkontroll, wheel/sdist-build och CLI --check passerar, exit 0. Lokala dokumentlänkar giltiga. TeamPlayer Done version 16 återläst; epicbeskrivning synkas separat med färsk version. Fortsätt E-03/F-10. Se [slutreview](docs/reviews/E-02.md).

### Task F-05 Skapa och återfinn epic och task worktrees

**Epic/fas/prioritet:** E-02 / 2 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `9974ec4e-453c-4498-8994-f14e119c6e2d`.

**Körbar:** Levererad — granskad och integrerad till E-02 via 5efca19.

**Källa:** A §§8–10, 30, 36; W §§3, 6, 10, 40. **Berör:** Git-adapter, paths, operationer, runreferenser.

**Beroenden:** E-01. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera create_epic_worktree och create_task_worktree via argumentbaserade subprocess-anrop. Kontrollera repository, ID-koppling, branch och normaliserad path. Spara skapade resurser och återanvänd dem endast om ägarskap och bas kan verifieras.

**Resultat och kontrakt:** Epic skapas från aktuell main; task från aktuell epic. Planerings-ID kan användas under bootstrap, externa ID:n efter anslutning. Paths hålls under konfigurerad rot. Upprepat anrop med samma identitet returnerar samma resurs; befintlig resurs med annan ägare ger fel.

**Acceptans**

- [x] **F-05.A1:** Epic och två tasks får egna branches/worktrees och rätt bas-SHA utan ändringar i main.
- [x] **F-05.A2:** Två likadana anrop ger en enda branch/worktree och samma referens.
- [x] **F-05.A3:** Path utanför roten, fel epic eller upptagen branch avvisas utan att befintligt arbete ändras.

**Verifiering:** Temporära Git-repositories och paths med mellanslag, traversal och befintliga worktrees.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Historisk blockerare, löst (2026-10-07):** E-01 är verifierad på epic-branchen men saknar PR/main-merge eftersom GitHub-push avvisas med Internal Server Error. F-05 har inte implementerats eller fått något task-worktree. Coordinator ansvarar för återläsning och E-01-integration enligt docs/reviews/E-01.md; först därefter återgår F-05 till Pending/Planned och kan plockas. Arbetsordningen ändras inte.

**Återställd för start:** TeamPlayer Pending återläst efter E-01 PR #1 och slutverifierad main-merge. Nästa steg: förbered feature/epic-e02 och task/e02-f05, kontrollera färsk version/tilldelning och plocka F-05.

**Implementerat kontrakt F-05:** WorktreeService är en intern service med betrodd Actor; Coordinator skapar epic och Integration tasks i sin registrerade epic. Planerings-ID:n/UUID:n ger deterministiska branches och paths under konfigurerad rot. Ny skapelse kräver ren aktuell källbranch och tillåten epicfas. Run och PENDING-operation sparas före Git; UUID-ägarmarkör i lokal Git-config verifieras tillsammans med repository/common-dir, path, branch och exakt bas. Samma intent återfinns efter avbrott; okända resurser adopteras inte. Saknad färdig resurs eller ändrad ofärdig bas ger avstämningsfel. Current_commit och SUCCEEDED sparas atomiskt. Git-arvsmiljö och hooks kan inte styra anropen. Ingen merge, cleanup eller agentstart ingår.

**Verifieringsunderlag F-05 (2026-10-07):** Python 3.13.14, Git 2.39.5. `uv run --locked pytest`: 88 passerade, exit 0; Ruff och diffkontroll: exit 0. 24 nya prov använder riktiga temporära Git-repositories och SQLite-filer: en epic/två tasks från aktuella baser, paths med mellanslag, replay efter omstart, ocommittat Worker-arbete, samtidiga identiska skapelser, fel roll/project/epic, upptagen branch/path, traversal/symlänk, avbrott före Git/efter branch/efter worktree/efter databasskrivningar för både epic och task, saknad/felägande resurs, ändrad mainbas, checkout-hook och otillåten scopeändring under slutreview. E-02.A1 är verifierat; A2/A3 återstår som samlad Git-integration i F-07/F-08. Bootstrap Integration-review godkänd för task `ff0cdc94adcc902eb5def3dcdd5bbc82c11b3150` mot epic `09624975e89acc7119daae1f6c7bf005eadf26d1`. --no-ff-merge `5efca19e574beb9970399ca52555e985e0d332ac`; 88 integrationstester, Ruff, diffkontroll och wheel/sdist-build passerar efter merge, exit 0. TeamPlayer Done återläst.

### Task F-06 Leverera diff och aktuella Git fakta för granskning

**Epic/fas/prioritet:** E-02 / 2 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `56e322eb-8902-4e39-92f0-66ed47224ee7`.

**Arbetsstart F-06 (2026-10-07):** TeamPlayer InProgress återläst. Bootstrap Integration verifierade konto, beroenden, ren epicbranch och bas `5cc0969c23be8a2c16300b33d76112d815bbcf06`. Worker arbetar i `task/e02-f06`, `.worktrees/task-e02-f06`. Det befintliga bootstrap-worktreet E-02 saknar WorktreeService-ägarskapsrecord och adopteras inte; task-worktreet skapades genom GitAdapter efter separata branch/path/baskontroller. Ingen parallell implementationstask startas.

**Körbar:** Levererad — granskad och integrerad i E-02 med --no-ff; TeamPlayer Done återläst.

**Källa:** A §§8, 20–22, 25, 36; W §§19–20, 31. **Berör:** Git-adapter, granskningsunderlag.

**Beroenden:** F-05. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera läsning av HEAD, commitexistens, branchinnehåll, arbetsläge, ändrade filer och diff mellan angivna baser. Returnera strukturerade fakta med task- och epic-SHA som review kan hänvisa till; implementera ingen merge.

**Resultat och kontrakt:** Underlaget skiljer commitdiff från ocommittade filer och innehåller verifierad repository/worktree-identitet. Stora diffar får inte tyst kapas som om hela ändringen granskats; redovisa begränsning och hur full diff hämtas.

**Acceptans**

- [x] **F-06.A1:** En känd commit ger rätt ändrade filer, diff och bas/current-SHA.
- [x] **F-06.A2:** En okänd commit eller branch från annat repository ger tydligt fel.
- [x] **F-06.A3:** Smutsigt worktree och ofullständig diff markeras så att underlaget inte kan godtas som färdig review.

**Verifiering:** Fixture-repository med flera branches, ocommittad fil, binär fil och diff över dokumenterad storleksgräns.

**Implementerat kontrakt F-06:** GitAdapter verifierar repository/common-dir, registrerad worktree/branch och fullständiga commit-ID:n. Immutable snapshot skiljer commitdiff, staged/unstaged och ospårade filer samt redovisar branchens basinnehåll, ändrade filer och binärpatch. Diffgräns 1 MiB (valbar 1 byte–64 MiB); hela outputens byteantal/hash och fullständigt argumentkommando bevaras även vid ofullständigt preview. GitReviewService binder underlaget till betrodd Integration/Coordinator och F-05-ägarskap, aktuell epic/main och source/target-SHA. Dirty, saknad bas, ändrat underlag eller ofullständig diff förhindrar reviewable. Ingen merge, approval, MCP-registrering eller schemaändring ingår. Underlaget är inte ett atomiskt lås; kommande integrationsoperationer måste återvalidera aktuella fakta.

**Första Worker-verifiering F-06 (2026-10-07):** Python 3.13.14, Git 2.39.5. `uv run --locked pytest`: 115 passerade, exit 0; `uv run --locked ruff check .`, diffkontroll och `uv build`: exit 0 (wheel/sdist). 27 nya prov använder riktiga temporära Git-repositories/worktrees och SQLite: exakta SHA/filer/diff/hash, främmande repository/branch/commit/blob, unsafe revisionsargument, binär- och specialfilnamn, rename, separata staged/unstaged/ospårade filer, diff över 1 MiB med verifierad full återhämtning, stale commit/bas, ändrad HEAD/epic/index/ägare, roll/scope, main-underlag, avstängd external diff/textconv/Git-miljö och oförändrat index. Acceptansen inväntar bootstrap Integration-review, Task → Epic-merge och integrationsverifiering.

**Review/fix F-06:** Bootstrap Integration återskapade en dold arbetsändring med assume-unchanged som Git-status inte visade. Worker korrigerar underlaget så assume-unchanged/skip-worktree i källa eller mål uttryckligen blockerar reviewable; submoduleändringar får inte döljas av diff.ignoreSubmodules. Tasken behåller Testing/Active genom loopen. Ny commit och verifiering krävs före godkännande.

**Korrigerad Worker-leverans F-06 (2026-10-07):** `uv run --locked pytest`: 119 passerade, exit 0, varav 31 Git-reviewprov; Ruff, diffkontroll och wheel/sdist-build passerar. Reviewfyndet är korrigerat och provat med riktiga assume-unchanged/skip-worktree-flaggor i källa/mål samt konfiguration som försöker dölja en committad gitlink. Nytt READY_FOR_REVIEW krävs för senaste commit; ingen approval eller Done registreras för första leveransens SHA.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Slutleverans F-06 (2026-10-08):** Bootstrap Integration-review godkänd för task `9765303e3faa42dfdbd2866328d09a4ba880d8ed` mot epic `5cc0969c23be8a2c16300b33d76112d815bbcf06`. Komplett slutdiff: 43625 bytes, SHA-256 `9678af05e7d3824a2e2fbc8619d8adcb9c031babc95f8b0dacb2cd6344e817e7`. Task → Epic --no-ff-merge `aa373bd7e9f4c8bec71d7a9482382432a5c4a5ce`; 119 pytesttester, Ruff, diffkontroll och wheel/sdist-build passerar på faktisk merge, exit 0. F-06.A1–A3 uppfyllda, TeamPlayer Done version 5 återläst. E-02 förblir Active med 1/3 egen acceptans; F-07 är nästa körbara task. Se [review och verifiering](docs/reviews/F-06.md).

### Task F-07 Synkronisera task mot epic och integrera granskad task

**Epic/fas/prioritet:** E-02 / 2 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `4bc70580-208a-4c06-a18a-2adce002a5f7`.

**Arbetsstart F-07 (2026-10-08):** TeamPlayer InProgress återläst, konto och beroenden verifierade. Worker använder `task/e02-f07` i `.worktrees/task-e02-f07` från epicbas `9a1118864c0db9b0ef9d376af5b59d09dbb75099`. Det äldre bootstrap-epicworktreet adopteras inte till produktens ägarskapsregister; GitAdapter används med verifierad branch/path/bas.

**Körbar:** Levererad — granskad och integrerad i E-02; TeamPlayer Done version 6 återläst.

**Källa:** A §§8–9, 21, 27, 36; W §§20, 23–25, 40. **Berör:** Git Manager, taskmerge, mergeunderlag.

**Beroenden:** F-06. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera sync_task_with_epic och merge_task_to_epic. Separera synkronisering till task från leveransmerge, kontrollera roll och commits och använd --no-ff vid leverans. Registrera operationens resultat även om efterföljande statusuppdatering misslyckas.

**Konkreta kontrakt F-07 / D-05:** Sync går Epic → Task och ogiltigförklarar äldre approval när underlaget ändras; leverans går Task → Epic med --no-ff. Beständig Operation sparas före Git och commit binds till operationens ID och exakta parents för återläsning efter avbrott. Minimal repo-låsning och SQLite skyddar orchestratorns kritiska sektion; samtidiga manuella Git-ändringar ger avstämningsfel. Tester körs som operatörskonfigurerat argv under betrodd Integration; faktisk exitkod och source/target-SHA sparas, utan rå output. Manuell registrerad review kan bara godkänna detta färska test-/Git-underlag. Merge litar inte på en agents tests_passed-flagga och sätter inte Done; fas 5/10 bygger vidare på dessa primitiver.

**Resultat och kontrakt:** Integration-rollen begär operationen; Worker mergear inte. Slutmerge kräver rent arbetsläge, godkännande och tester för exakt task-SHA och aktuell epic-SHA. Konflikt ger spårbar blockerare i task-worktree. Git-merge ensam sätter inte task till Done.

**Acceptans**

- [x] **F-07.A1:** Task B kan synkroniseras efter att task A integrerats; gamla godkännandet kan inte användas efter ändrad bas eller taskcommit.
- [x] **F-07.A2:** Godkänd task integreras med mergecommit; samma operation kan återläsas utan andra mergecommit.
- [x] **F-07.A3:** Fel roll, smutsigt arbetsläge eller konflikt blockerar leveransmerge och lämnar tasken ej Done.

**Verifiering:** Temporärt repository med två parallella tasks, konflikt, stale approval och avbrott efter Git-merge.

**Worker-leverans F-07 (2026-10-08):** GitIntegrationService levererar synk, verklig argv-testkörning, registrerad manuell review och skyddad leveransmerge. Repo-flock/SQLite, PENDING-intent, exakta commits, operationstagg/parents och idempotenta återläsningar ger recovery utan dubbel merge. Konflikter och dolda arbetsändringar bevaras/blockerar leverans; test-/reviewreportflagga kan inte ersätta verkligt underlag. Ingen Task Done eller automatisk reviewagent införs. `uv run --locked pytest`: 141 passerade, exit 0 (22 nya Gitintegrationsprov). Ruff, diffkontroll och wheel/sdist-build passerar. Git, SQLite och subprocess-testkommandon är verkliga; runtime-handoff är testfixtures och ingen verklig Codex-runtime påstås här. Acceptansen inväntar Integration-review, no-ff-merge och verifiering på epicen.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Slutleverans F-07 (2026-10-08):** Bootstrap Integration-review godkänd för task `c3929c07c49a8495b0d7bb5a77b6971c3831000a` mot epic `9a1118864c0db9b0ef9d376af5b59d09dbb75099`. Komplett reviewdiff: 56115 bytes, SHA-256 `442ce3a48316859d4ca69921009ee9073159a3a0acf2498722d245eb12bf3473`. --no-ff-merge `a6a59531aee67f0b2c963a86d946941ce5a6dabb`; 141 pytesttester, Ruff, diffkontroll och wheel/sdist-build passerar på faktisk merge, exit 0. F-07.A1–A3 uppfyllda, TeamPlayer Done version 6 återläst. E-02 är fortsatt Active med 1/3 egen acceptans; F-08 är nästa. Se [review och verifiering](docs/reviews/F-07.md).

### Task F-08 Integrera godkänd epic till aktuell main

**Epic/fas/prioritet:** E-02 / 2 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `a9c700e9-6200-4aa7-a19f-3b1535270e57`.

**Körbar:** Levererad — granskad/integrerad i E-02; TeamPlayer Done version 7 återläst.

**Arbetsstart F-08 (2026-10-08):** TeamPlayer InProgress version 2 återläst; utförare och beroenden verifierade. Bootstrap Worker i `task/e02-f08`, `.worktrees/task-e02-f08`, bas `1e4d9b329bb04a6a0806bb132aed7e20d47a081b`. Första add_worktree-anropet avvisades före Git (saknad branch_exists); korrigerat anrop skapade och verifierade rätt worktree innan kodändring. Äldre bootstrap-resurser adopteras inte.

**Källa:** A §§8–9, 25–27, 36; W §§29–33, 40. **Berör:** Git Manager, epicmerge, slutverifiering.

**Beroenden:** F-07. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera merge_epic_to_main med Coordinator-policy och komplett mergeunderlag. Kontrollera att alla tasks är integrerade och att review/tester avser aktuella epic- och maincommits. Persistéra merge-SHA; lämna epicstatus till service som kan verifiera resultatet.

**Konkreta kontrakt F-08:** Coordinator kör operatörskonfigurerad faktisk aggregate-/slutverifiering som argv. Scope binds till en explicit task-ID-lista från betrodd Coordinator-konfiguration; samtliga måste vara Done med registrerad F-07-merge, exakta parents och faktisk ancestry i epicen. Manuell review registreras som Operation för aktuella epic/main-SHA, testexitkod och taskmanifest; ändrad main kräver separat synk och ny verifiering. Pending-intent och operationstagg/parents ger idempotent recovery. Main-merge registrerar SHA och lämnar Epic MERGING; separat faktisk slutverifiering sparas även vid fel och sätter inte automatiskt Done. Ingen automatisk review eller scheduling. Schema 2 återanvänds.

**Resultat och kontrakt:** En manuellt utfärdad och registrerad Coordinator-review används under bootstrap; fas 10 producerar den automatiskt. Ändrad main kräver synkronisering och ny relevant verifiering. Konflikter eskaleras. Misslyckad slutverifiering efter merge lämnar spårbart ej-Done-läge och blockerar nästa epic.

**Acceptans**

- [x] **F-08.A1:** Aktuellt godkännande och taskunderlag ger --no-ff-merge till main med registrerat SHA.
- [x] **F-08.A2:** Integration/Worker, ofärdig task eller ändrad main avvisas utan merge.
- [x] **F-08.A3:** Avbrott efter merge kan avstämmas via operation och Git; det skapas ingen dubbel merge eller falsk Done.

**Verifiering:** Temporärt repository med aktuellt/föråldrat main, otillåtna roller och felinjicerad verifiering.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Worker-leverans F-08 (2026-10-08):** READY_FOR_REVIEW för task/e02-f08. EpicIntegrationService levererar verklig aggregate-/main-verifiering, manuell review, main→epic-synk och Coordinator-only --no-ff-merge. Explicit scope och taskens registrerade review/test/merge/parents/ancestry kontrolleras; Pending-intent och operationstagg möjliggör recovery utan dubbel merge. Sluttestfel sparar SHA och lämnar MERGING, inte Done. `uv run --locked pytest`: 163 passerade, 22 nya epicintegrationsprov; Ruff, diffkontroll och wheel/sdist-build passerar, exit 0. Git/SQLite/subprocesser är verkliga, runtime-handoff är fixtures. Tidigare F-07-Kanbanrad korrigerad till redan verifierad Done 3/3. Taskens egen review, merge och integrationstest återstår.

**Slutleverans F-08 (2026-10-08):** Bootstrap Integration godkänner `33eda4a976e85610f8b941c13b9fddffd20b2022` mot `1e4d9b329bb04a6a0806bb132aed7e20d47a081b`. Komplett diff 50932 bytes, SHA256 `ab387921585d2ad2d7d6d84ffc952d45d70c52026f312c39d8846681d3f98479`. Faktisk Task → Epic --no-ff-merge `1d6e2bea6fe9ff64cc0159eafe751c365becbf2f`; 163 pytesttester, Ruff, diffkontroll och wheel/sdist-build passerar på merge, exit 0. F-08.A1–A3 uppfyllda; TeamPlayer Done version 7 återläst. E-02 Active med 3/3 egen acceptans på epic-branchen, men F-09 och slutlig PR/main-integration/verifiering återstår. Se [review](docs/reviews/F-08.md).

### Task F-09 Avsluta Git resurser efter verifierad leverans

**Epic/fas/prioritet:** E-02 / 2 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `96091ef5-be75-4d81-af5f-ffceda85b50c`.

**Körbar:** Levererad — granskad/integrerad i E-02; TeamPlayer Done version 7 återläst.

**Arbetsstart F-09 (2026-10-08):** TeamPlayer InProgress återläst, konto och beroenden verifierade. Worker i `task/e02-f09`, `.worktrees/task-e02-f09`, bas `6aebea8777acc3227b699318da44ee4b831e6e7e`. Äldre bootstrap-resurser adopteras inte och utvecklingsworktrees städas inte av produktproven.

**Källa:** A §§8, 10, 36; W §§25–26, 38, 40. **Berör:** Git-adapter, resurspolicy, dokumentation.

**Beroenden:** F-07, F-08. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera försiktig remove_worktree och eventuell branchradering enligt dokumenterad retention. Kontrollera integrerat arbete, arbetsläge, ägarskap och att ingen aktiv körning använder resursen. Behåll run-, review- och mergehistorik.

**Konkreta kontrakt F-09 / retention:** Integration får explicit remove_task_worktree efter Done och faktisk registrerad taskmerge/review/test med aktuella Gitparents/ancestry. Repo-lås och SQLite skyddar kontroll/intention; Pending-operation före Git ger återförsök efter remove. Endast ägd taskpath under konfigurerad rot tas bort med Git worktree remove utan force. Dirty, ignored/ospårade filer, osäker index, pågående Git, ändrad branch/HEAD/ägare, annan resursägare eller Worker-slot blockerar. Registrerad runtime kräver bekräftad inaktivitet från betrodd adapter; frånvarande/okänd probe ger avstämningsbehov. Saknad resurs godtas bara med tidigare cleanup-intent/resultat. Branches och all run/review/mergehistorik behålls; branchradering och epiccleanup automatiseras inte. Schema 2 återanvänds.

**Resultat och kontrakt:** Cleanup är explicit och återförsökbar. Ointegrerat eller smutsigt arbete och resurser som används av annan run får inte tas bort. Saknad redan städad resurs ger ett känt resultat. Sessionsstopp och slotrelease kopplas in i Worker-livscykeln senare.

**Acceptans**

- [x] **F-09.A1:** Verifierat integrerad och oanvänd taskresurs tas bort utan att historik eller main ändras.
- [x] **F-09.A2:** Ointegrerat arbete, smutsigt worktree eller aktiv resurs skyddas från borttagning.
- [x] **F-09.A3:** Upprepad cleanup lyckas utan nya sidoeffekter; retentionbeslutet finns i driftinstruktionen.

**Verifiering:** Temporära worktrees med integrerade, ointegrerade och ocommittade ändringar samt upprepat cleanupanrop.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Worker-leverans F-09 (2026-10-08):** READY_FOR_REVIEW för task/e02-f09. TaskCleanupService kontrollerar registrerad faktisk leverans, F-05-ägarskap, exklusiv path/branch, inaktivitet och rent/olåst arbetsläge inklusive ignored-filer. Pending-intent före Git-remove utan force ger recovery med branch/historik bevarade; saknad okänd eller återuppstånden resurs raderas inte. Leveransbevis återanvänds av F-08 utan semantikändring. 24 cleanup-prov samt 22 epicintegrationsprov passerar (46 unika relevanta kontroller), Ruff/diffkontroll/wheel/sdist-build passerar, exit 0. Git/SQLite/subprocesser är verkliga; runtime-handoff/probe är fixtures, inget verkligt Herdr-stopp påstås. Hela suites integrationsgrind körs efter merge.

**Slutleverans F-09 (2026-10-08):** Bootstrap Integration godkänner `f9869a2b012ad11fcaef1543f02fd7e6d5f518b3` mot `6aebea8777acc3227b699318da44ee4b831e6e7e`. Komplett diff 38254 bytes, SHA256 `222c4bf956135205fbd77cf5f86d6468c3922c10ec7b1087300cc1f1e4abd708`. Faktisk Task → Epic --no-ff-merge `65097a826f5724f8be6750449e43a7381edcf72a`; 187 pytesttester, Ruff, diffkontroll och wheel/sdist-build passerar på merge, exit 0. F-09.A1–A3 uppfyllda; TeamPlayer Done version 7 återläst. E-02 Active med alla fem tasks Done och 3/3 egen acceptans; samlad slutreview, PR/main-merge och slutverifiering återstår. Se [review](docs/reviews/F-09.md).

## Epic E-03 Starta och återanslut agentruntime genom Herdr

**Fas:** 3. **Prioritet:** P1. **Kanban-status:** Done. **TeamPlayer Epic-ID:** `3b52b7d6-7527-4d44-a873-238f26067246`.

**Körbar:** Levererad — PR #3, main-merge och slutverifiering genomförda. **Beroende:** E-02 Done på main.

**Källa:** A §§7, 19, 31, 37; W §§6, 8, 10, 17, 38.

### Resultat och omfattning

Operatören kan skapa workspace och starta Codex i rätt worktree, skicka ett uppdrag, läsa status och återansluta eller stoppa den registrerade sessionen.

**Ingår:** Gränssnittsverifiering, Herdr/Codex-adaptrar, runtime-ID och ett verkligt integrationsprov. **Utanför:** Implementationsuppdrag, task-review och Kanban-automation.

**Gemensamma regler:** tillämpa samtliga sex kontrakt ovan och de ingående taskernas förvillkor. **Risk och beslutspunkt:** Tillgängliga API:er och resume/park-förmåga måste provas. F-10 dokumenterar vad runtime faktiskt stöder innan beroende kod byggs.

### Tasks och beroenden

| Task | Leverans | Taskberoenden utöver epicens beroende | Prioritet |
| --- | --- | --- | --- |
| F-10 | Verifiera Herdr och Codex gränssnitt | E-02 | P1 |
| F-11 | Skapa workspace och starta Codex i rätt worktree | F-10 | P1 |
| F-12 | Skicka uppdrag och observera start och status | F-11 | P1 |
| F-13 | Återanslut och stoppa registrerad runtime | F-12 | P1 |

### Epicacceptans

- [x] **E-03.A1:** Ett verkligt worktree kan kopplas till Herdr och Codex med beständiga workspace/pane/agent/session-ID.
- [x] **E-03.A2:** Uppdrag och status kan utväxlas utan manuell terminalinteraktion.
- [x] **E-03.A3:** Återanslutning och stopp berör rätt session; misslyckad start skapar inte en dold extra agent.

Epicen följer dessutom den gemensamma definitionen av Done. Acceptansen verifieras genom taskernas underlag och ett samlat prov av epicens resultat.

**Samlad verifiering E-03 (2026-10-08):** F-10–F-13 Done efter granskad Task→Epic-merge och tester. Egen acceptans 3/3: F-13:s faktiska F-05→F-11→F-12→F-13-serviceprov binder samma Git-worktree till beständiga runtime/session-ID, transport/native ACK/event utan manuell input och verifierat stopp/park/resume av samma session. F-11:s fel-/partial-/samtidighetsprov hindrar dolda dubbelstarter. På aktuell samlad merge `1a16aea9`: 262 tester/Ruff/build/diff/CLI passerar. [Samlat verkligt underlag](docs/runtime/F-13-prover.json). Alla testservrar stoppade. Coordinator slutreview och PR/main/slutverifiering genomförda enligt slutleveransen nedan.

**Slutleverans E-03 (2026-10-08):** Egen acceptans 3/3 och alla fyra tasks Done. Coordinator APPROVED `9a40965ce99c21df27ab4676e7489b05ec1a5899` mot main `1108b0f2dd8d587ecdb6deb702210cb14e02d2a2`; full diff 230965 bytes/SHA256 `f5436e4b05162a005350b7074c73704e0096f616f28bdb6c681c28b1694c2e1d`. [PR #3](https://github.com/johanLstang/HerdrCoordinator/pull/3), faktisk main-merge `0395728141b35f1e8fa3d7696c2467c634b3f56b`, exakta parents och identisk granskad tree verifierade. På faktisk main passerar 262 pytesttester (262.14 s), Ruff, wheel/sdist-build, diff och CLI --check exit 0. TeamPlayer Done återläst; beskrivning synkas med färsk version. [Slutreview](docs/reviews/E-03.md). Nästa E-04/F-14 från uppdaterad main.

### Task F-10 Verifiera Herdr och Codex gränssnitt

**Epic/fas/prioritet:** E-03 / 3 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `fd25939b-3ae9-4215-8edc-dd3415a696b5`.

**Körbar:** Ja — användaren har godkänt trust för exakt testrepo; fortsätt samma registrerade pane.

**Arbetsstart F-10 (2026-10-08):** HerdrCoordinator Write och blitterbot@gmail.com återverifierade; alla beroenden Done och E-02 PR #2/main 9beaf34 slutverifierad (187 tester). E-03 InProgress/Active och F-10 InProgress återlästa. Worker i `task/e03-f10`, `.worktrees/task-e03-f10`, från aktuell main/epicbas `1108b0f2dd8d587ecdb6deb702210cb14e02d2a2`. Utvecklingsbootstrap använder verifierad GitAdapter enligt AGENTS.md; inga produkt-/runtimefakta fabriceras.

**Källa:** A §§7, 31, 37; W §§6, 10–12, 17, 38. **Berör:** Adapterkontrakt, miljöprov, dokumentation.

**Beroenden:** E-02. **Externa förutsättningar:** X-01: Herdr och Codex ska vara installerade och åtkomliga i testmiljön med ett ofarligt testrepository.

**Arbetsinstruktion för Codex:** Inventera installerade Herdr/Codex-versioner och tillgängliga gränssnitt. Prova workspace, pane, start, prompt, status, återanslutning, stopp och möjlig parkering i testmiljö. Skriv ett adapterkontrakt med verifierade kommandon/anrop och kända begränsningar.

**Resultat och kontrakt:** Arkitekturens logiska operationsnamn översätts till verkliga anrop här. Dokumentera transport, signal för startbekräftelse, sessions-ID och kapabiliteter för roll/sandbox. Spara provresultat utan credentials; ej stödd funktion blir konkret blockerare.

**Acceptans**

- [x] **F-10.A1:** Varje erforderlig adapteroperation har verifierat anrop/resultat eller specificerad blockerare.
- [x] **F-10.A2:** En ny testsession kan identifieras och återanslutas med beständigt ID.
- [x] **F-10.A3:** Parkering, startbekräftelse och möjliga sandboxgränser redovisas separat från antaganden.

**Verifiering:** Manuellt avgränsat verkligt prov med inspelade sanerade resultat och versionsuppgifter.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Hinder F-10 (2026-10-08):** Herdr 0.9.3/Codex 0.161.0 provade i separat server `hc-f10-20261008`, workspace `w1`, pane `w1:p1`, agent `hc-f10-codex`. Start gav `agent_not_ready`; get/read bekräftar Codex “Trust this folder?” för `.herdr/probes/f10/repo`. Herdrs inbyggda skill kräver användarinput före dialogbeslut; beslut begärt, ingen input skickad. Promptprov avvisades korrekt `agent_blocked`, ingen dubbel start. Verkliga app-server sandboxprov passerar skriv-/nätkontroller men tillåter läsning utanför cwd. Nästa ansvariga roll: operatören beslutar om trust, därefter fortsätter samma agent med prompt/session/resume/stopp/park. F-11 blockeras av F-10; ingen ändrad arbetsordning. [Sanerat kontrakt och prov](docs/runtime/Herdr-Codex-kontrakt.md). Ingen acceptans markerad som komplett; E-03 förblir Active.

**Återupptagning F-10 (2026-10-08):** Användaren godkände uttryckligen trust för denna testrepo. TeamPlayer InProgress återupptaget med färsk version; trust-dialogen godkänd i samma pane via explicit agentnamn. Tidigare hinder ovan är historik; nästa steg är faktisk sessions- och livscykelverifiering.

**Verifiering F-10 (2026-10-08):** Trust-beslutet är löst av användaren. Verkliga Herdr/Codex-prov omfattar readiness, korrelationssvar, native sessions-ID, processstopp, samma-ID-resume och interrupted-turn. Isolerad `--no-daemon` väljs; alla fem registrerade processidentiteter försvann efter stopp. Private-session `01a11ab7-a01c-7e70-8203-e7a26abd3526` återupptogs med exakt tidigare markör. App-server read/resume bekräftar ID/cwd/historik/readOnly och completed/completed/interrupted. Sandboxprov verifierar skriv-/nätgränser men medger läsning utanför cwd. Testservern är bekräftat stoppad, produktservern orörd. [Kontrakt](docs/runtime/Herdr-Codex-kontrakt.md) och [sanerat JSON-underlag](docs/runtime/F-10-prover.json). Acceptans 3/3 verifierad; Integration-review, faktisk merge och dokumentkontroller passerar.

**Leverans F-10:** Bootstrap Integration APPROVED task `0f103bf0bbcfcbe1b74109d1ce7edb662c2da717` mot epic `1108b0f2dd8d587ecdb6deb702210cb14e02d2a2`; faktisk --no-ff-merge `7dfc54b3376e6699233e261688a6503b623243c0`, exakta parents/tree återlästa. Dokument-/länk-/JSON-provkonsistens och diffkontroll passerar på merge. TeamPlayer Done återläst. [Review](docs/reviews/F-10.md). E-03 Active, egen acceptans 0/3; fortsätt F-11.

### Task F-11 Skapa workspace och starta Codex i rätt worktree

**Epic/fas/prioritet:** E-03 / 3 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `784fbbcf-dcf5-475d-940f-bb4039cf40fe`.

**Körbar:** Ja — F-10 verifierad/integrerad, E-02 Done på main och X-01 verifierad i samma testrepo.

**Källa:** A §§7, 19, 37; W §§6, 10, 38. **Berör:** Herdr/Codex-adapter, TaskRun/EpicRun, operationer.

**Beroenden:** F-10. **Externa förutsättningar:** X-01 och verifierat kontrakt från F-10.

**Arbetsinstruktion för Codex:** Implementera verifierade workspace/pane- och sessionstarter med kontrollerad cwd och beständig koppling till run. Spara externa ID:n efter varje lyckat delsteg och återfinn dem vid upprepat anrop; skicka ännu inga produktuppdrag.

**Resultat och kontrakt:** Startoperationen kontrollerar worktreeägare och förväntad branch. Fel efter skapad workspace lämnar den registrerad för avstämning. En befintlig matchande session återanvänds; osäkert ägarskap blockerar start.

**Acceptans**

- [x] **F-11.A1:** En session startar i tilldelat worktree och alla tillgängliga runtime-ID:n sparas.
- [x] **F-11.A2:** Dubbel start ger samma ägda session utan extra agent.
- [x] **F-11.A3:** Fel cwd och fel efter workspace-skapande redovisas utan falsk startbekräftelse.

**Verifiering:** Adapterprov med fel efter varje delsteg samt verkligt startprov enligt F-10.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start/kontrakt F-11 (2026-10-08):** Task plockad med verifierad User-tilldelning och alla TeamPlayer-beroenden Done. `task/e03-f11` från aktuell epic `e7bdf34eb142838ab7a66a9a70769c747e354376`. Använd det [verifierade runtimekontraktet](docs/runtime/Herdr-Codex-kontrakt.md): explicit Herdr-session, codex --no-daemon, operatörsvald sandbox, branch/path/ägare före start. Beständig STARTING/slotreservation före sidoeffekter; inga produktuppdrag eller WORKING i denna task. Workspace- och agent-ID sparas efter kända svar. Efter okänd skapandeskrivning återförsök inte; efter okänt startutfall återläs endast känd pane/agent och bekräfta faktisk process/cwd/readiness. Saknat nytt Codex-ID före första turn förblir nullable och fylls av senare verifierad signal.

**Verifiering F-11:** 26 kontrollerade startprov passerar på aktuell kod; tidigare full regression 211 passerade före de sista branch-/cwdkontrollerna. Verkliga Herdr/Codex-start/repeat/reopen med F-05-registrerade Git/SQLite-test-runs använder samma agent, slot och workspace/tab/pane/terminal/process-ID. Ingen produktprompt skickad, Codex-ID nullable före första turn. Kodens CodexAdapter thread/read verifierad separat mot bevarad F-10-session. Readiness bekräftas via actual /proc argv/cwd/startTime; kända partialfel och CLI-stderr hanteras. Ruff, diffkontroll och wheel/sdist-build passerar. [Start-/recoverykontrakt](docs/runtime/F-11-start.md), [sanerade prov](docs/runtime/F-11-prover.json). F-11 acceptans 3/3. Integration APPROVED `dacc4738a2a8ad487b3a87da998b99fefa7b9eb6` mot `e7bdf34eb142838ab7a66a9a70769c747e354376`; faktisk no-ff-merge `4c9956ac7e64de0f87f6f2b8230a344164ed753f`, exakta parents/tree återlästa. På merge passerar 213 pytesttester, Ruff, build, diff och CLI --check, exit 0. TeamPlayer Done version 7 återläst. [Review](docs/reviews/F-11.md). E-03 Active; fortsätt F-12.

### Task F-12 Skicka uppdrag och observera start och status

**Epic/fas/prioritet:** E-03 / 3 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `8f1c732d-8ada-4e39-b2f1-b140f2795517`.

**Körbar:** Ja — F-11 integrerad/verifierad på epicen och E-02 Done på main; X-01/statuskontrakt verifierade.

**Källa:** A §§7, 19–20, 37; W §§10–11, 15, 19. **Berör:** Prompttransport, statusadapter, events.

**Beroenden:** F-11. **Externa förutsättningar:** X-01 och statusmekanism verifierad i F-10.

**Arbetsinstruktion för Codex:** Implementera sändning av strukturerat uppdrag, detektion av Worker-bekräftelse och statusobservation. Knyt inkommande meddelanden till registrerad session/run. Definiera säkert beteende vid utebliven, upprepad eller för gammal signal.

**Resultat och kontrakt:** Prompt levereras till rätt pane/session med korrelations-ID. Levererad text betyder inte att arbete startat. Transporthändelse och domänövergång hålls separata; okänd avsändare eller otolkbar status leder inte till WORKING eller READY_FOR_REVIEW.

**Acceptans**

- [x] **F-12.A1:** En skickad prompt når rätt session och matchande startbekräftelse blir ett verifierbart event.
- [x] **F-12.A2:** Utebliven bekräftelse ger timeout/fel och lämnar tasken före WORKING.
- [x] **F-12.A3:** Dubbel eller främmande status accepteras inte som nytt giltigt resultat.

**Verifiering:** Kontrollerad transport med duplicerade/försenade signaler samt verkligt prompt/statusprov.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start/kontrakt F-12 (2026-10-08):** Integration plockade tasken med verifierad User-tilldelning och Done-beroenden; Worker i `task/e03-f12` från epic `2b23924fc6cbab3ccc65c79a66f6a0c54c962393`. Beständig unik dispatchoperation per run, nonce/korrelations-ID och prompt-hash före sändning. Matchande WORKING-ACK måste vara ett faktiskt Codex-agentmeddelande från registrerad session och ny turn/item efter sparad baslinje, med exakt project/epic/run/task/korrelation. Generisk Herdr-status eller promptens egen text räcker inte. Okänt transportutfall observeras utan blind omsändning; timeout håller STARTING/slot. Dubletter registrerar inget nytt event. API är internt; implementation/taskreview/TeamPlayer-automation tillhör senare epics.

**Verifiering F-12:** 27 aktuella uppdragsprov passerar (24.59 s); tidigare kombinerat prov 51 start-/transportfall passerade. Ruff, build och diffkontroll exit 0. Verklig F-05/F-11-start, prompt/hash/ACK från Codex-session `01a11af6-8719-7763-a6d8-2a50050b8574`, turn/item/event och upprepning/SQLite-reopen verifierade utan ny prompt. Explicit testserver stoppad efter agent frånvarande/shell-only/processidentiteter borta; historik och slotclaim bevarade. [Uppdragskontrakt](docs/runtime/F-12-uppdrag.md), [sanerade prov](docs/runtime/F-12-prover.json). Acceptans 3/3. Integration APPROVED `cc10306209133a06d99416fbd971167364c8ae68` mot `2b23924fc6cbab3ccc65c79a66f6a0c54c962393`; faktisk --no-ff-merge `06d64898f3a47856ca4f403ab469a6f261fcc1e6`, exakta parents/tree verifierade. På merge passerar 240 tester (234.72 s), Ruff/build/diff/CLI, exit 0. TeamPlayer Done version 7 återläst. [Review](docs/reviews/F-12.md). Fortsätt F-13; E-03 Active.

### Task F-13 Återanslut och stoppa registrerad runtime

**Epic/fas/prioritet:** E-03 / 3 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `83a64bd8-6bfd-4988-82dd-40f3aca590a5`.

**Körbar:** Ja — F-12 verifierad/integrerad på epicen, E-02 Done på main och X-01 verifierad.

**Källa:** A §§7, 28, 37; W §§8, 17–18, 26, 38. **Berör:** Herdr/Codex-adapter, runtime-livscykel.

**Beroenden:** F-12. **Externa förutsättningar:** X-01; park/resume måste vara styrkt innan F-31 och F-32 kan verifieras live.

**Arbetsinstruktion för Codex:** Implementera återanslutning till registrerad Codex-session och idempotent stopp av ägd agent. Förbered verifierad park/resume-kapabilitet för fas 8. Dokumentera start, stop, timeout och skillnaden mellan tappad anslutning och förlorad session.

**Resultat och kontrakt:** Återanslutning skapar inte ny task, branch eller worktree. Saknad session rapporteras och kan bara återupptas via runtime som verifierats stödja det. Stoppa inte en annan run; okänt stoppresultat måste avstämmas innan resursen betraktas som fri.

**Acceptans**

- [x] **F-13.A1:** Återanslutning använder samma session-ID och korrekt worktree.
- [x] **F-13.A2:** Upprepat stopp av redan stoppad ägd session är säkert och påverkar inte en annan agent.
- [x] **F-13.A3:** Saknad session och obekräftat stopp ger tydliga resultat som kan eskaleras.

**Verifiering:** Adapterprov samt verkligt start–återanslut–stopp med dokumenterade runtimebegränsningar.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start/kontrakt F-13 (2026-10-08):** Task plockad med verifierad User-tilldelning och Done-beroenden; Worker i `task/e03-f13` från epic `d84792256fa2ee656fcdd7b78e6a4331327467f4`. Återanslut levande ägd runtime med observation, utan ny start. Stopp journalför intent och Linux PID/startTime, barn och foreground-processgrupper före signaler; registrerat namn, pane och process måste matcha. Ingen input till blocked/unknown-dialog. Aktiv turn avbryts före TUI-exit; agent frånvarande, shell-only och samtliga fångade identiteter/grupper inaktiva krävs före slotrelease. Aktiv task stoppas till PARKED via konkret BLOCKED-orsak; resumefas/session/worktree bevaras. Explicit resume från bekräftat stopp reserverar slot före exakt Codex resume UUID, ingen ny konversation. Okänt start/stopp/resultat observeras utan ersättningsresurser; nya generationer har eget operations-ID. Full Attention-policy/scheduling och svarstransport tillhör senare features.

**Verifiering F-13:** 22 aktuella lifecycleprov passerar (20.69 s); F-11:s 26 startprov och F-12:s 27 uppdragsprov passerade med adapterändringen. Ruff/build/diff exit 0. Verklig F-05→F-11→F-12→F-13-livscykel styrks för Codex-session `01a11b07-3768-7651-b2c2-46e2ff817af8`: same-session reconnect/reopen, två verifierade stopp/park/slotrelease och resume till samma branch/worktree/fas utan ny konversation. Varje stopp fångade fem processidentiteter och tre grupper; namngiven testserver stoppad. Barn som lever kvar håller slotclaim i kontrollerat prov. [Livscykelkontrakt](docs/runtime/F-13-livscykel.md), [sanerade prov](docs/runtime/F-13-prover.json). Acceptans 3/3. Integration APPROVED `fc3f8e51a74ed64dc35b8f036533ef7bf8a9ed4a` mot `d84792256fa2ee656fcdd7b78e6a4331327467f4`; faktisk no-ff-merge `1a16aea96e310d8267f1cf50333057e5e9f0d201`, exakta parents/tree återlästa. På merge passerar 262 tester (257.66 s), Ruff/build/diff/CLI exit 0. TeamPlayer Done version 7 återläst. [Review](docs/reviews/F-13.md). E-03 Active; fortsätt samlad epicreview och PR/main-integration.

## Epic E-04 Låt en Worker leverera en verifierbar task

**Fas:** 4. **Prioritet:** P1. **Kanban-status:** Done. **TeamPlayer Epic-ID:** `242ffa18-da4e-4496-8e9f-c4c1b4d8315c`.

**Körbar:** Ja — E-03 Done på main; externa villkor anges per task. **Beroende:** E-03 Done på main.

**Källa:** A §§19–20, 31, 38; W §§10–12, 15, 19, 38–40.

### Resultat och omfattning

Operatören kan lämna en explicit task och få committad implementation samt verifierad READY_FOR_REVIEW från en ensam Worker.

**Ingår:** Taskinmatning, Worker-prompt, start, rapportkontrakt och ett verkligt taskprov. **Utanför:** Automatisk review, merge, TeamPlayer-koppling och två parallella Workers.

**Gemensamma regler:** tillämpa samtliga sex kontrakt ovan och de ingående taskernas förvillkor. **Risk och beslutspunkt:** En agentrapport kan vara felaktig. F-16 jämför rapporten med Git och F-17 provar faktiska runtime- och behörighetsgränser.

### Tasks och beroenden

| Task | Leverans | Taskberoenden utöver epicens beroende | Prioritet |
| --- | --- | --- | --- |
| F-14 | Beskriv ett Worker uppdrag och rapportkontrakt | E-03 | P1 |
| F-15 | Starta en explicit task med en Worker | F-14 | P0 |
| F-16 | Verifiera Worker rapport mot committat arbete | F-15 | P0 |
| F-17 | Verifiera en verklig Worker leverans | F-16 | P1 |

### Epicacceptans

- [x] **E-04.A1:** En manuellt angiven task leder till arbete i eget worktree och giltig committad överlämning.
- [x] **E-04.A2:** READY_FOR_REVIEW registreras först efter oberoende kontroller av commit, arbetsläge och testunderlag.
- [x] **E-04.A3:** Ingen automatisk merge utförs och tasken blir inte Done i Worker-MVP.

Epicen följer dessutom den gemensamma definitionen av Done. Acceptansen verifieras genom taskernas underlag och ett samlat prov av epicens resultat.

### Task F-14 Beskriv ett Worker uppdrag och rapportkontrakt

**Epic/fas/prioritet:** E-04 / 4 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `10bfff5f-ba3a-4f10-8510-f8578da7a747`.

**Körbar:** Ja — E-03 PR #3/main 0395728 slutverifierad; inga ytterligare externa villkor.

**Källa:** A §§20, 31, 38; W §§11–12, 19. **Berör:** Taskspecifikation, Pydantic-schema, Worker-prompt.

**Beroenden:** E-03. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera validerad lokal taskinmatning och versionerat uppdrags/rapportformat. Skapa Worker-prompt med mål, krav, acceptans, källor, task/epic-ID, branch, worktree och bascommit. Dokumentera D-04 och hur äldre textvarianter normaliseras.

**Resultat och kontrakt:** Task hör till exakt en epic. Rapport innehåller status, task/run-ID, branch, commit, sammanfattning, tester, ändrade filer och begränsningar. BLOCKED innehåller orsak och inputbehov. Metadata får inte tolkas som kommandon; prompten ger inte rollbehörighet.

**Acceptans**

- [x] **F-14.A1:** En komplett lokal task ger ett reproducerbart uppdrag med alla källor och avgränsningar.
- [x] **F-14.A2:** Saknad acceptans, ogiltig epic-koppling eller okänd rapportversion avvisas innan start.
- [x] **F-14.A3:** STATUS/TASK-varianter från källexemplen normaliseras bara om identiteten kan verifieras.

**Verifiering:** Schema- och promptprov med normalfall, saknade fält och rapporter från båda dokumentens exempel.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start/D-04 F-14 (2026-10-08):** Task plockad med verifierad User-tilldelning och Done-beroenden, `task/e04-f14` från aktuell main/epic `92be75b6812a8f2f1e8f11aa892322fd81e21b92`. Lokal taskspec och uppdrag använder schema/prompt version 1; slutrapporter version 1 med READY_FOR_REVIEW eller BLOCKED. E-03:s korrelerade WORKING-ACK är separat transportkontrakt. Rapportmetadata är påståenden: sessionproveniens kommer från betrodd adapter, Git/testbevis verifieras i F-16. Legacy STATUS/TASK/TASK_ID normaliseras endast med matchande registrerad session och run/epic/task/branch; kort commit kräver matchande full observerad taskcommit, annars avvisas. Okänd version/extra eller dubbla JSON-/textfält och fel identitet avvisas. Worker-policy versioneras under prompts och paketeras; data JSON-kodas och körs inte som kommandon.

**Worker READY_FOR_REVIEW F-14 (2026-10-08):** 35 schema-/prompt-/rapporttester passerar (0.54 s), Ruff, wheel/sdist-build och diffkontroll exit 0. Installerad/extraherad wheel innehåller och läser den identiska Worker-policyn utan checkout-fallback. D-04 dokumenteras med tre genererade scheman och validerat lokalt exempel i [kontraktet](docs/worker/F-14-kontrakt.md). Ingen runtime/state/Gitbevisverifiering införs. Integration review/merge och regression återstår; tasken förblir Active.

**Integration/verifierad leverans F-14 (2026-10-08):** APPROVED cef6a699e8358e205d57ae2751d3303cf9f8adb7 mot epic 92be75b6812a8f2f1e8f11aa892322fd81e21b92; komplett diff 57213 bytes/SHA256 dc6225be8db9a848ffbc64a010c49800def2ae8f6ef98aa65f1a5f06e98917b6. Faktisk --no-ff Task → Epic 258010c5b655d103df3f385ce17984983c892b9b, exakta parents och identisk granskad tree verifierade. 297 pytesttester passerar (263.25 s), Ruff/build/diff/CLI --check exit 0. Kriterier A1–A3 verifierade genom 35 kontraktprov. TeamPlayer Done återläst; [review](docs/reviews/F-14.md). E-04 Active med egen acceptans 0/3 till samlad Worker-leverans. Nästa F-15.

### Task F-15 Starta en explicit task med en Worker

**Epic/fas/prioritet:** E-04 / 4 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `1d8feb08-94fe-42a1-a2ca-fefba83cc40d`.

**Körbar:** Ja — F-14 verifierad och mergad till aktuell epic; E-03 Done på main.

**Källa:** A §§19, 29–30, 38; W §§10, 15, 40. **Berör:** Taskservice, MCP, Git/Herdr, persistens.

**Beroenden:** F-14. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera task_start för explicit task med en slot i denna fas. Validera roll och task, claima atomiskt, skapa resurser, starta session och leverera prompt. Registrera varje delsteg och sätt WORKING först efter bekräftelse.

**Resultat och kontrakt:** Flöde PLANNED → CLAIMED → STARTING → WORKING. CLAIMED/STARTING reserverar slot enligt D-03. Redan påbörjad task returnerar befintlig run. Delvis skapade resurser avstäms vid retry; en annan task eller session får inte tillägnas.

**Acceptans**

- [x] **F-15.A1:** Giltig task får en enda run, korrekt bas, eget worktree och bekräftad Worker.
- [x] **F-15.A2:** Samtidiga eller upprepade starter av samma task skapar inte fler resurser.
- [x] **F-15.A3:** Startfel lämnar sparade delsteg och tasken ej WORKING; ingen extra slot kan bokas för att kringgå gränsen.

**Verifiering:** Integrationsprov i temporärt Git med kontrollerad Herdr-adapter, dubbla starter och fel efter varje sidoeffekt.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start/kontrakt F-15 (2026-10-08):** Verifierad User-tilldelning och alla beroenden Done, task/e04-f15 från epic b7c058f. Lokal version-1-spec valideras före mutation; task-run, immutable spec/prompt och slot 1 sparas atomiskt före Git/Herdr. Phase 4 har en Worker även om grundkonfiguration tillåter två. F-05 får separat intentförberedelse utan Git-mutation; F-11/F-12 används för resurser och native ACK. Upprepning observerar samma run/journal och okända externa resultat skickas inte blint igen. MCP task_start aktiveras endast med betrodd operatörskonfiguration av explicit Herdr-session, använder inline spec och anslutningens registrerade Integration-identitet. Inga statusbevis från Worker-argument. Externa förutsättningar kräver betrodd preflight; lokala beroenden kräver faktisk Done-leverans. Full Worker-isolering/verklig leverans verifieras i F-17; ingen automatisk merge införs.

**Worker READY_FOR_REVIEW F-15 (2026-10-08):** 29 taskstartprov och F-11/F-12:s 53 prov passerar (82 total, 57.12 s); F-05/MCP:s befintliga 35 prov passerar i tidigare riktad körning. Tre CLI-prov verifierar explicit session/Integration-konfiguration, saknad faktisk Herdr-miljö och inget implicit runtime-start. Ruff/build/diff passerar. [Start- och recoverykontrakt](docs/worker/F-15-start.md). Verkligt Git/SQLite med kontrollerad runtime; faktisk Worker-leverans/full policy F-17. Integration review/merge/regression återstår.

**Integration/verifierad leverans F-15 (2026-10-08):** APPROVED `f150f5cfa370faefe70b3bd906e57d3cb8564ffd` mot `b7c058f391dbc681d647c791b5cbaf64d51f74c0`; full diff 54007 bytes/SHA256 `a30ea944044933f55fe0e90579aeed4bbbcda206c46ea44e0262b22fe38bae91`. Faktisk Task → Epic --no-ff `a61f181d9890bd58f01baa83413b8d6e6b613a1b`, exakta parents och identisk granskad tree. 329 tester på faktisk merge (284.66 s), Ruff/build/diff/CLI exit 0. TeamPlayer Done återläst. [Review](docs/reviews/F-15.md). E-04 Active, egen acceptans 0/3; faktisk Worker/policy återstår i F-17. Nästa F-16.

### Task F-16 Verifiera Worker rapport mot committat arbete

**Epic/fas/prioritet:** E-04 / 4 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `472fbfc1-4e51-4eba-bfbe-490edb090546`.

**Körbar:** Ja — F-15 verifierad och mergad till aktuell epic; E-03 Done på main.

**Källa:** A §§20–22, 38; W §§19–20, 25. **Berör:** task_report_ready, Git-fakta, testunderlag.

**Beroenden:** F-15. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera rapportmottagning för egen task. Verifiera commitexistens, branchinnehåll, rent worktree och testunderlag; spara validerad överlämning. Ta emot blockerarrapport utan att bygga hela parkflödet i denna task.

**Resultat och kontrakt:** READY_FOR_REVIEW är möjligt efter WORKING och med matchande run/session. Testunderlag ska ange kommando, exitkod och commitkoppling; agentens PASS-sträng är inte ensam verifiering. Okänt eller otillräckligt underlag blockerar review. Full Attention/park kommer i E-08.

**Acceptans**

- [x] **F-16.A1:** En giltig commit och spårbart testunderlag ger READY_FOR_REVIEW med Kanban-mappning Active.
- [x] **F-16.A2:** Påhittad SHA, smutsigt worktree, främmande task eller misslyckat test avvisas.
- [x] **F-16.A3:** Duplicerad överlämning återanvänds; tasken mergeas inte och blir inte Done.

**Verifiering:** Git-fixtures och rapportprov med falska SHA, fel ägare, ocommittat arbete och kända testutfall.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start/kontrakt F-16 (2026-10-08):** Verifierad User och Done-beroenden, task/e04-f16 från bf36c58. Slutrapport hämtas av betrodd Codex-adapter från registrerad session/cwd och en avslutad native agentMessage efter F-12:s ACK; inga avsändar-/statusbevis från Workerargument. Integration kan collect; Worker-MCP task_report_ready/blocked anger bara eget tasktarget, inte godtycklig rapport eller testkommando. F-14 normaliserar innehåll, F-05/F-06 kontrollerar ägare/HEAD/rent worktree. Operatörskonfigurerat test-argv körs oberoende och binds till faktisk commit/epicbas före READY. FAILED/PENDING testbevis ger ingen READY och upprepas inte blint; en betrodd Integration kan begära explicit nytt verifieringsförsök. Native blockerarrapport sparar reason/input, BLOCKED/Attention behåller slot/session; full park/release återstår. Handoff/operation/event/proveniens sparas idempotent utan merge/Done.

**Worker READY_FOR_REVIEW F-16 (2026-10-08):** 32 rapport-/Git-/MCP-/proveniens-/recoveryprov och 22 F-07-integrationsprov passerar (54 total, 73.65 s). Riktad CLI/MCP/startup-körning passerade (64 total, 48.57 s) före de två sista ACK-/unknown-test-proven. Ruff/build/diff exit 0. [Rapportkontrakt](docs/worker/F-16-rapporter.md). Operatörens bounded test-argv/timeout i TOML; subprocess får minimal miljö och hash binds till argv/miljö. Exempelkonfiguration använder en Worker under fas 4. Ingen merge/Done/slotrelease i produktservicen; full faktisk Worker och filesystem-/process-/testisolering F-17. Integration review/merge/regression återstår.

**Integration/verifierad leverans F-16 (2026-10-08):** APPROVED `e6ec1fec2de7beef458d22c16c79d6d84d655c1a` mot `bf36c581bb8f87bf292c77787d7cd1564c42451a`; full diff 54591 bytes/SHA256 `389606c8c7df0cb5bc235306889095abe1f3caf1c93b7ab5809393a28253da5b`. Faktisk Task → Epic --no-ff `575c8efa9d536039860d593be28dc1ace93dd3b5`, exakta parents och identisk granskad tree. 361 tester på faktisk merge (319.11 s), Ruff/build/diff/CLI exit 0. TeamPlayer Done återläst. [Review](docs/reviews/F-16.md). E-04 Active, egen acceptans 0/3; verklig Worker och behörighetsprov F-17 återstår.

### Task F-17 Verifiera en verklig Worker leverans

**Epic/fas/prioritet:** E-04 / 4 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `9ae60f57-9194-4bf8-8763-d56cddfdc868`.

**Körbar:** Ja — F-16 verifierad/mergad, E-03 Done på main och X-01 tidigare verifierad; aktuella runtimevillkor återkontrolleras före prov.

**Källa:** A §§31, 38, 50; W §§11–12, 19, 40. **Berör:** Worker-policy, runtimekonfiguration, manuellt integrationsprov.

**Beroenden:** F-16. **Externa förutsättningar:** X-01 och möjligheten att köra ofarliga runtimebehörighetsprov.

**Arbetsinstruktion för Codex:** Kör en liten ofarlig implementationstask genom hela Worker-MVP i testrepository. Aktivera och dokumentera verifierade runtimebegränsningar från D-02/F-10. Samla Git-, run- och testunderlag och beskriv exakt vilka gränser som tekniskt kan upprätthållas.

**Resultat och kontrakt:** Ett eget worktree och promptregler ersätter inte OS/sandboxkontroll. Orchestratorns kritiska verktyg ska neka Worker start av annan task och merge. Om runtime kan kringgå nödvändiga gränser via shell registreras blockerare inför autonom drift i E-10; gränsen får inte redovisas som tekniskt säkrad.

**Acceptans**

- [x] **F-17.A1:** En verklig Worker producerar ändrad kod, commit och verifierad överlämning i rätt task-worktree.
- [x] **F-17.A2:** Main och epic är oförändrade; ingen automatisk merge sker.
- [x] **F-17.A3:** Otillåtna orchestratoranrop avvisas och faktisk filesystem/kommandobehörighet redovisas med provresultat.

**Verifiering:** Verkligt taskprov med före/efter-SHA, sandbox/policykontroll och kontroll av andra worktrees.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start/kontrakt F-17 (2026-10-08):** Task/e04-f17 från epic a73db1d med verifierad User och Done-beroenden. Verklig liten Python/unittest-Worker i redan godkänt F-10-testrepo, explicit server hc-f17-20261008 och F-05→F-15→F-16. Promptleveransen får inte vänta på hela implementationen före ACK-observation: Herdr använder --wait --until working --timeout enligt verifierad CLI-grammatik; anropet väntar på arbetsstart, F-12 observerar korrelerad native ACK separat före deadline. Faktiska Git-/filesystem-/kommandobegränsningar redovisas med ofarliga prover; olösta gränser registreras konkret inför E-10-autonomi enligt featurens kontrakt, inte som säkrad isolering. Ingen testmain-/epicmerge. Startbanner avfärdas manuellt i provets operatörspreflight; det ändrar inga säkerhetsinställningar. Misslyckade prov bevaras och F-13 stoppar/verifierar inaktivitet före slotrelease. Ingen blind omleverans efter timeout. Installerad Codex thread/read i separat metadata-child rekonstruerar pågående native turn som interrupted; ACK får godtas där endast med samtidigt verifierad fysisk working-runtime och oförändrad session/process samt exakt ny userprompt och korrelerad agentACK. Det är startbevis, aldrig slutrapport/Done. Avslutad rapport kräver fortsatt completed-turn och idle runtime.

**Worker-underlag F-17 (2026-10-08):** Verklig native Worker-run `25bf44fa-2aff-45d0-b57d-e732fc722dce`, ACK under working före deadline och commit `5e23126269027af55e9fcb0d5a528238129792eb`. Sex riktiga unittest + oberoende F-16-verifiering gav READY_FOR_REVIEW; duplicate EXISTING utan ändring. Fixture main 7ce621a och epic 5027484 oförändrade, samtliga provworktrees rena, ingen merge/Done. F-13 bekräftade stopp/inaktivitet/slotrelease. [Verkligt prov och gränser](docs/worker/F-17-verklig-worker.md), [native underlag](docs/worker/F-17-prover.json). Bevarade misslyckade försök; inga återställda deadlines/retroaktiva ACK. Verifierad MCP-Worker nekas start/merge/främmande scope. Outside-read tillåts; faktisk Git-eskaleringsbegäran accepterades; F-16-testsubprocess kan skriva utanför worktree. Dessa gränser är inte säkrade. **Hinder före F-38/E-10-autonomi:** skyddad DB/config/credentials, kontrollerad Git/approval, isolerade tester och input-readiness måste verifieras i målmiljön av Coordinator/operatör och Integration. F-17 review/integration återstår.

**Worker READY_FOR_REVIEW F-17 (2026-10-08):** 74 riktade ACK/rapport/MCP-prov passerar (77.39 s); tidigare ACK/taskstart 59 prov (62.10 s). Ruff, wheel/sdist-build, diff och faktiskt testserverstopp exit 0. Fyra nya ACK-projektionstester verifierar working, idle/done och främmande ACK; timeoutregressionen kvarstår. Verklig Worker, oberoende sex-test-handoff och negativa kapabilitetsprov enligt underlaget ovan. Hinder inför F-38 registrerat i både backlogg och TeamPlayer utan att starta den tasken. Integration review/merge/regression återstår.

**Integration/verifierad leverans F-17 (2026-10-08):** APPROVED `7e0f8bbb13db8c74531dc0d7d5c64fa621bbf280` mot `a73db1d9c886fd8fdf4a920827179f704edae349`; komplett diff 56594 bytes/SHA256 `e55305faa71472f2140c2dcd4e389213cebb6c6ac217ec7846233db1cf921392`. Faktisk --no-ff Task → Epic `fe3eb71f34103da4e0a20ff55e24d3a34aca2f79`, exakta parents/tree. 365 tester (312.03 s), Ruff/build/diff/CLI exit 0 på faktisk merge. TeamPlayer Done återläst. [Review](docs/reviews/F-17.md). E-04 Active, egen acceptans 3/3 från samlad verklig Worker-MVP; PR/main/slutverifiering återstår. Föregående review/väntan ovan är nu avslutad historik.

**Coordinator/verifierad slutleverans E-04 (2026-10-08):** APPROVED `e570eb7fef4e8026d8be6000b5bddc113af1355e` mot `92be75b6812a8f2f1e8f11aa892322fd81e21b92`; full diff 230015 bytes/SHA256 `170c61b2c0e2b65f7e86e4a2dd5f2ee017ac9dc0150ac6b570b85bba64ba6b5c`. [PR #4](https://github.com/johanLstang/HerdrCoordinator/pull/4), faktisk mainmerge `56c5aff928e4d093c2507c39afcc28b96e3229ba`, exakta parents/tree. `uv run --locked pytest`:365 passed (311.53s) på faktisk main, Ruff/build/diff/CLI exit0. E-04 Done återläst i TeamPlayer, egen acceptans3/3. [Slutreview](docs/reviews/E-04.md). Tidigare PR/main-väntan är avslutad historik; F38 runtimegate kvarstår. Nästa E-05/F-18 från verifierad main.

## Epic E-05 Granska korrigera och integrera en task

**Fas:** 5. **Prioritet:** P1. **Kanban-status:** Done. **TeamPlayer Epic-ID:** `9d4ff24d-71a6-4e02-9498-bbbaee7e1db8`.

**Körbar:** Ja — E-04 verifierad/Done på main; externa villkor anges per task. **Beroende:** E-04 Done på main.

**Källa:** A §§20–22, 31, 39; W §§20–25, 38–40.

### Resultat och omfattning

En task kan gå från Worker-överlämning genom granskning och korrigering till verifierad integration i epic-branchen.

**Ingår:** Reviewkontext, Integration-policy för review, feedback till samma Worker, versionsbunden approval och task-Done. **Utanför:** Långlivad Integration Agent som själv schemalägger hela epicen samt Coordinator-slutreview.

**Gemensamma regler:** tillämpa samtliga sex kontrakt ovan och de ingående taskernas förvillkor. **Risk och beslutspunkt:** Review kan bli inaktuell under parallellt arbete. F-18 och F-20 binder underlag och beslut till exakta commits. En granskande Integration-session används innan E-09 etablerar dess hela livscykel.

### Tasks och beroenden

| Task | Leverans | Taskberoenden utöver epicens beroende | Prioritet |
| --- | --- | --- | --- |
| F-18 | Bygg komplett reviewkontext från aktuell epic | E-04 | P0 |
| F-19 | Återför konkret reviewfeedback till samma Worker | F-18 | P1 |
| F-20 | Bind taskgodkännande till granskat underlag | F-19 | P0 |
| F-21 | Sätt task Done efter merge och integrationstester | F-20 | P0 |
| F-22 | Verifiera review och fix till integrerad task | F-21 | P1 |

### Epicacceptans

- [x] **E-05.A1:** En verklig task går genom READY_FOR_REVIEW → CHANGES_REQUESTED → korrigering → APPROVED.
- [x] **E-05.A2:** Tasken synkroniseras och mergeas mot rätt epiccommit och integrationstester passerar innan Done.
- [x] **E-05.A3:** Testfel, konflikt eller ändrat reviewunderlag kan inte ge Done eller använda ett gammalt godkännande.

Epicen följer dessutom den gemensamma definitionen av Done. Acceptansen verifieras genom taskernas underlag och ett samlat prov av epicens resultat.

### Task F-18 Bygg komplett reviewkontext från aktuell epic

**Epic/fas/prioritet:** E-05 / 5 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `6dfd789c-25d6-465b-bb75-a81e4b5d4a34`.

**Körbar:** Ja — E-04 verifierad och Done på main; inga ytterligare externa villkor.

**Källa:** A §§21–22, 39; W §§20, 23. **Berör:** Reviewservice, Git, testkörning, Review.

**Beroenden:** E-04. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera task_review_request. Synkronisera task mot aktuell epic, kör konfigurerade relevanta tester och bygg kontext med taskspecifikation, acceptans, diff, ändrade filer, källor och epicregler. Spara underlagets identitet och båda commits.

**Resultat och kontrakt:** Review går READY_FOR_REVIEW → REVIEWING först när aktuell bas och komplett underlag finns. Konflikt och testfel hindrar godkännandeflödet. Testkommandon kommer från betrodd projektkonfiguration, inte ett fritt shellkommando i agentrapporten.

**Acceptans**

- [x] **F-18.A1:** Granskaren får samtliga specificerade underlag och aktuella task/epic-SHA.
- [x] **F-18.A2:** En task med äldre bas synkroniseras och testas före review; tidigare testresultat ersätter inte det nya provet.
- [x] **F-18.A3:** Konflikt, ofullständig diff eller testfel ger explicit ej-godkännbar review utan merge.

**Verifiering:** Temporärt repository med basändring och konflikt; kontrollerad testprocess med både pass och fail.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start/kontrakt F-18 (2026-10-08):** Task/e05-f18 från aktuell main/epic a0937fa efter E04 Done/PR4/main56c5aff slutverifierad med365tester. Konto/User/Write och beroenden återverifierade. task_review_request blir Integration-only och får enbart egen task samt stabil request_key; inget caller-testkommando/roll/commit/epicregler. Immutable F15-spec och faktisk F16-handoff krävs. Betrodd operatörskonfiguration review_context anger version1 projekt/epic, requirements, acceptans och versionerade repo-källor; saknad/fel context ger inget REVIEWING. F07 sync/ny verifiering återanvänds, native uppdrag/handoff bevaras. Reviewpaketet sparar komplett lossless diff, changed files, lokala källblobbar, testoperation och exakta task/epic-SHA samt context-ID/hash. Fullt aktuellt godkänt testunderlag krävs före REVIEWING; konflikt/testfel/trunkering ger explicit ej-godkännbar context utan approval/leveransmerge/Done. Repeat återanvänder samma giltiga underlag; stale/unknown kräver avstämning eller explicit ny request_key. Schema2/operationsjournal, ingen autonom granskare eller TeamPlayer-produktadapter i denna task. F17:s runtimegate inför F38 kvarstår.

**Worker F-18 READY_FOR_REVIEW (2026-10-08):** Branch task/e05-f18. Komplett journalförd reviewkontext och Integration-only MCP, operator-konfiguration, versionsbundna Git-källor och recovery enligt [kontraktet](docs/review/F-18-kontext.md). 32 riktade review/CLI-tester passerade (68.13 s), 54 befintliga Git-/rapporttester (72.17 s), samt Ruff/build/diff/CLI --check exit0. Verklig Git/SQLite/testprocess/MCP; kontrollerad Herdr/Codex-adapter. Inget produktapproval/leveransmerge/Done eller nytt native Worker-prov påstås. Integration-review, faktisk Task → Epic-merge och samlad verifiering återstår; kriterier/Done markeras först därefter.

**Verifierad leverans F-18 (2026-10-08):** F-18 APPROVED 2129e00bc2e021a70b9687e481e1b52334749beb mot a0937fa70614df5ce0006cb60b4caaead6c5fe8c; diff 69236 bytes/SHA256 7181591fc8ba407b760473fef31a6223f44911c47ac438ca92a99402a2e20640; Task→Epic --no-ff b2410b74fa68e86bda4eb9cf78ea06ab9740eab7, operation 17232e5a-8011-4163-b7dc-ae359ba32263, exakta parents och identisk granskad tree. 32 riktade review/CLI-tester (68.13 s), 54 Git-/rapporttester (72.17 s). På faktisk merge: 397 passed (380.33 s); Ruff/build/diff/CLI exit0. A1: full spec/acceptans, aktuella versionerade källor, lossless diff/changed files och testpins/context-ID. A2: verklig Epic→Task-synk och nya tester före review. A3: konflikt/trunkering/testfel explicit ej-godkännbar utan leveransmerge/Done. Recovery efter verifiering, unknown test utan resend, samtidiga dubbletter och stale epic/config verifierade. Git/SQLite/testprocess/MCP verkliga; Herdr/Codex-adapter kontrollerad. F17:s autonomigate inför F38 består. Sekventiell bootstrap Integration/GitAdapter; inga fabricerade produktruns. E05 Active, egen acceptans0/3 tills samlat verkligt review/fix/integrationsprov. Se [reviewunderlaget](docs/reviews/F-18.md). TeamPlayer Done återläst med korrekt User-tilldelning.

### Task F-19 Återför konkret reviewfeedback till samma Worker

**Epic/fas/prioritet:** E-05 / 5 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `9deec5f8-e93d-4b71-9996-956049931e18`.

**Körbar:** Ja — F-18 verifierad/Done på epic och E-04 Done på main; separat, betrodd runtime finns för det avgränsade provet.

**Källa:** A §§21–22, 31, 39; W §§20–22, 38. **Berör:** Integration-prompt, reviewbeslut, feedbacktransport.

**Beroenden:** F-18. **Externa förutsättningar:** X-01 för det verkliga reviewprovet.

**Arbetsinstruktion för Codex:** Skapa Integration Agent-policy för taskreview och validera granskarens strukturerade beslut. Implementera task_request_changes med numrerade problem och berörda acceptanskriterier. Spara Review och skicka feedback till samma Worker-session.

**Resultat och kontrakt:** CHANGES_REQUESTED behåller Kanban Active och återgår till WORKING när Worker bekräftar korrigeringen. Nya commits kräver ny överlämning och review. Extern beslutspunkt redovisas som blockerare; komplett parkering kopplas in i E-08. Granskaren får inte implementera Worker-tasken eller mergea main.

**Acceptans**

- [x] **F-19.A1:** Ett negativt beslut sparar reviewnummer, commits, feedback och berörda kriterier.
- [x] **F-19.A2:** Korrigeringen görs i samma session, branch och worktree och kan lämnas till ny review.
- [x] **F-19.A3:** Tom feedback, fel task eller Worker som försöker godkänna sin egen task avvisas.

**Verifiering:** Reviewserviceprov med kontrollerad transport och verkligt begränsat review/fix-prov när runtime finns.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start/kontrakt F-19 (2026-10-08):** Task/e05-f19 från verifierad epic ed3f85e efter F18 Done, review2129e00/mergeb2410b7/397tester. Konto/Write/User och beroenden återlästa. Integration-only task_request_changes validerar version1 CHANGES_REQUESTED-beslut mot sparat aktuellt F18 context-ID; problem numreras sammanhängande och hänvisar till befintliga acceptanskriterier. Reviewnummer, exakta SHA och strukturerad feedback sparas före transport. Betrodd runtimebindning, slot, samma native Codex-session/branch/worktree och idle krävs före första dispatch. Korrelerad native ACK krävs före CHANGES_REQUESTED→WORKING; okänd leverans återobserveras utan blind resend eller återställd deadline. Nya rapporter måste komma efter korrektions-ACK och genomgå F16/F18 på nytt. Versionerad Integration-reviewpolicy paketeras; inget reviewer-självimplementation, approval, leveransmerge, Done eller ny Worker-session. Kontrollerade negativa/recoveryprov och separat verkligt avgränsat native fixprov; F17-autonomigate inför F38 består.

**Kontraktsförtydligande F-19 (verkligt prov 2026-10-08):** READY_FOR_REVIEW.tests avser den levererade commiten och ska inte blanda in historiska röda TDD-prov. Tidigare felresultat redovisas ärligt i test_summary/summary. F16:s avvisning av rapporterad testfail bevaras; ingen failure filtreras bort automatiskt. Worker- och korrektionsprompt förtydligas. Första provattempten sparas PARKED/inaktiv efter att harness missat F15-parentcompletion; separat second-attempt använder hela F15 före F16/F18. En explicit, journalförd operatörsfråga för rapportformat kan skickas till samma verifierade redo session utan ny kodtask eller fabricerad ACK/READY.

**Worker F-19 READY_FOR_REVIEW (2026-10-08):** task/e05-f19. 27 review/fix/CLI-tester (94.11 s), 36 kontrakt/prompt-kontroller (6.65 s), Ruff/build/diff/CLI exit0. Verkligt [Herdr/Codex-prov](docs/review/F-19-verklig-korrigering.md): review1/operationcababba0 → samma session/branch/worktree-ACK → korrigerad native commit2b26b470 → nio tester, ny verifierad handoff och F18-context6dafbc49. Konflikt/historiska testclaims/harnessfel avvisas och bevaras; explicit rapportförtydligande, ingen automatisk filtrering. Native test-Worker bekräftat stoppad/slotnull, serverstoppad. A1–A3 styrks av verkligt prov samt separata MCP-/negativa-/recovery-/raceprov. Produktapproval/leveransmerge/Done och F17-autonomigate återstår utanför F19; manuell taskreview och --no-ff Task → Epic/integrationsgate återstår före Done.

**Verifierad leverans F-19 (2026-10-08):** F-19 APPROVED a049fefa49c8fa0927afde7940aa582d0077abdb mot ed3f85e9c6a320d01fd9bcb786907ea80b5db169; full diff 88246 bytes/SHA256 92e7f6a6de854dbba30d6a9bf1e409c697d8be2a5aaacb859d23e0a258caefd8; faktisk --no-ff Task→Epic 22ea85deed1ed9e797fed3fbd4a66b780ee38783, operation d6795829-0531-441d-9175-9616ae0134da, exakta parents och identisk granskad tree. 27 review/fix/CLI-tester (94.11 s), 36 kontrakt/prompt-kontroller (6.65 s). På faktisk merge: 424 passed (460.14 s); Ruff/build/diff/CLI exit0. A1: current F18 context/testpar, Review1/exakta SHA och numrerad criteria-linked feedback före transport. A2: verklig native Worker samma SID01a11bb4, branch/worktree, commit16fd50→2b26b47, korrektionsACKcababba0, nio tester och ny oberoende handoff/context6dafbc49. A3: Worker/främmande context/criteria/slot/session, tom feedback/approval/injektion avvisas via service/MCP. Crash/unknown/no-resend/deadline/race verifierade. Historiskt röd rapport avvisad; explicit ärligt rapportförtydligande, första harnessfel bevarat/inaktivt. Test-Worker fysiskt stoppad innan slotrelease och namngiven testserver stoppad. Git/SQLite/test/MCP och native fix verkliga; negativa runtimeadaptrar kontrollerade separat. F17-autonomigate inför F38 består. Bootstrap Integration/GitAdapter; inga fabricerade produktruns. E05 Active, egen0/3 tills samlat F22-prov och main-integration. Se [reviewunderlag](docs/reviews/F-19.md). TeamPlayer Done återläst med korrekt User-tilldelning.

### Task F-20 Bind taskgodkännande till granskat underlag

**Epic/fas/prioritet:** E-05 / 5 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `5de9046f-4c8b-4f69-ae12-33fc5d213df3`.

**Körbar:** Ja — F-19 verifierad/Done på epic och E-04 Done på main; inga ytterligare externa villkor.

**Källa:** A §§14, 21–22, 39; W §§20, 23–25. **Berör:** Review, policy, versionskontroll.

**Beroenden:** F-19. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera APPROVED som ett beständigt granskningsbeslut från behörig Integration-roll. Kontrollera att allt obligatoriskt underlag finns och att task- och epic-SHA matchar. Ogiltigförklara användbarheten av äldre approval när kod eller bas ändras.

**Resultat och kontrakt:** Reviewhistorik bevaras även när ett godkännande blir inaktuellt. APPROVED är Active och en förutsättning för merge, inte Done. Granskningsunderlag ska koppla tester och acceptans till samma versionspar.

**Acceptans**

- [x] **F-20.A1:** Aktuell review från rätt roll ger APPROVED med versionsbundna referenser.
- [x] **F-20.A2:** Ny taskcommit eller ny epiccommit efter review hindrar användning av gamla approval.
- [x] **F-20.A3:** Workerbeslut, saknat testunderlag och approval för annan run avvisas utan statusframflyttning.

**Verifiering:** Serviceprov med ändrade commits, upprepat approval och förbjudna aktörer i ett temporärt repository.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start/kontrakt F-20 (2026-10-08):** task/e05-f20 från clean epic a651a25 efter F19 Done/reviewa049fef/merge22ea85d/424tester. Write/User blitterbot och beroenden återlästa. Integration-only task_approve tar egen task, stabil request_key och version1 APPROVED-beslut med sparat F18 context-ID, meningsfull summary och alla befintliga acceptanskriterier i verified_criteria; inga caller-SHA/testbooleans/roller. Gemensam aktuell contextvalidering återanvänds av negativt/positivt beslut och binder immutable spec, kompletterande källor, komplett diff och verklig verifieringsoperation till exakt task/epic-par och operator-konfiguration. Parentintent och authenticated reviewerprincipal sparas före F07:s review/APPROVED; delvis sparad review återläses med samma nyckel. Positivt/negativt beslut får inte samtidigt ta över samma Reviewing-task med oavstämd parentoperation. Approval binds till context-ID, review-ID, test-ID, task/epic-SHA och verifierad kriterielista; historik bevaras när source/base/config ändras, men användbarheten verifieras mot aktuella fakta och stale avvisas. APPROVED förblir Active/slotheld; inget merge, Done eller slotrelease i F20. Schema2/journal, strict MCP och kontrollerade Git/SQLite/recovery/raceprov; samlat native approve/mergeprov i F22. F17-runtimegate införF38 består.

**Worker F-20 READY_FOR_REVIEW (2026-10-08):** task/e05-f20. 27 approval/CLI-prov (78.43 s), 73 approval/changes/review/Git-prov (190.70 s) samt tre prov av senaste approval/recovery/race-underlaget (14.69 s) passerar. Ruff/build/diff/CLI exit0. A1: authenticated Integration/current complete context och beständiga review/test/SHA/criteria-referenser, Active/slotheld utan merge/Done. A2: ny task/epiccommit eller operator-konfiguration avvisar äldre approval med historik bevarad. A3: Worker/främmande run/context, saknat test/handoff, ofullständig criteria, injicerade bevis och ändrad retry-identitet avvisas. Crash efter faktisk APPROVED före parentcheckpoint återhämtar samma review; exklusiva positiva/negativa beslut och konkurrerande retries verifierade. Verkligt Git/SQLite/testprocess/MCP, kontrollerad runtimefixture; samlat native mergeprov F22 och F17-autonomigate införF38 kvarstår. [Kontrakt/recovery](docs/review/F-20-godkannande.md). Review/--no-ff Task→Epic och samlad integrationsgrind återstår före Done.

**Verifierad leverans F-20 (2026-10-08):** F-20 APPROVED e7e917c2510fee88558c98e0bd721b408094eeff mot a651a254c455b7361b10ec359a12bce5dd5dbe02; full diff 56464 bytes/SHA256 13ac7f81ee4c78cecb14201230c82cdf62588e390c53f542b62701e6b5593d54; faktisk --no-ff Task→Epic 9d6cadb5f80a636381eaafcdb59da524b2ab6763, operation 0bab8481-6d24-4a0b-94d2-6c338e6b4cd4, exakta parents och identisk granskad tree. 27 approval/CLI-prov (78.43 s), 73 approval/changes/review/Git-prov (190.70 s) och tre senaste proof/recovery/race-prov (14.69 s). På faktisk merge: 445 passed (540.48 s); Ruff/build/diff/CLI exit0. A1: authenticated Integration, komplett senaste immutable spec/context/sources/diff/handoff/aktuell testoperation; Review/parent/reviewer/context/test/SHA/criteria pins, Active/slotheld utan merge/Done. A2: nya task/epiccommits eller operator-konfiguration hindrar historisk approval med historik bevarad. A3: Worker/främmande run/context, saknat test/handoff, ofullständig criteria och injicerade proofargument avvisas utan framflyttning. Partial-review recovery, exakt samma nyckel/reviewer/decision, positivt/negativt race och concurrent idempotens verifierade. Verkligt Git/SQLite/testprocess/MCP; runtime kontrollerad fixture, samlat native approval/mergeprov F22. F17-autonomigate införF38 består. Bootstrap Integration/GitAdapter-undantag utan fabricerade produktruns. E05 Active, egen0/3 tills F22 och main-integration. Se [reviewunderlag](docs/reviews/F-20.md). TeamPlayer Done återläst med korrekt User-tilldelning.

### Task F-21 Sätt task Done efter merge och integrationstester

**Epic/fas/prioritet:** E-05 / 5 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `e931598a-4fa5-488e-aeea-0db91a570bdd`.

**Körbar:** Ja — F-20 verifierad/Done på epic och E-04 Done på main; inga ytterligare externa villkor.

**Källa:** A §§21, 30, 39; W §§24–26, 39–40. **Berör:** task_merge, Git, verifieringsprocess, taskstatus.

**Beroenden:** F-20. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Koppla versionsbunden approval till Git Managers taskmerge. Kör integrationskontroller på det integrerade resultatet, spara underlag och sätt DONE först vid framgång. Stoppa Worker och använd säker cleanup enligt resurspolicyn; synkning till TeamPlayer tillkommer i E-06.

**Resultat och kontrakt:** APPROVED → MERGING → DONE kräver faktisk merge-SHA och verifieringsresultat. Fel efter merge sparas och blockerar Done; avstämning kan verifiera om utan andra merge. Slotrelease kräver bekräftad inaktiv Worker. Äldre review används inte om mål-HEAD har ändrats.

**Acceptans**

- [x] **F-21.A1:** Godkänd task mergeas en gång och får DONE först efter godkända integrationstester.
- [x] **F-21.A2:** Ett fel efter Git-merge behåller merge-SHA men ger inte DONE eller osäker cleanup.
- [x] **F-21.A3:** Återförsök av känd merge verifierar befintligt resultat utan extra merge eller Worker.

**Verifiering:** Git/serviceintegration med fel före merge, efter merge, under test och under sessionsstopp.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start/kontrakt F-21 (2026-10-08):** clean task/e05-f21 från aktuell epic f808c97 efter F20 Done/reviewe7e917c/merge9d6cadb/445tester. Write/User/tilldelning/Done-beroenden återlästa. Integration-only task_merge anger eget tasktarget, stabil request_key och separat verification_key för ett explicit integrationstestförsök. F20:s slutförda aktuella approval och full reviewkontext återkontrolleras inom Git Managers kritiska mergeoperation; parentintent sparar autentiserad utförare, immutable approval/context/test/SHA-referenser före faktisk --no-ff Task→Epic. Endast en oavstämd taskleverans per epic. Känd merge återverifieras via journal, operationstagg, parents och ancestry utan att begära gamla premerge-HEAD som aktuell epic eller skapa extra merge/Worker. Operatörens betrodda test-argv körs på exakt merge-SHA i epicen med minimal miljö; SUCCEEDED kräver oförändrat rent source/target/resultat. Misslyckat eller okänt test återkörs inte med samma verification_key; explicit ny nyckel kan verifiera samma merge på nytt. F13 får ett separat leveransstopp, validerat mot faktisk merge och godkänd eftertestoperation, som bevarar MERGING och frigör slot först efter bekräftad fysisk inaktivitet. Done efter återkontroll av merge/test/stopp; partiellt fel sparar merge-SHA och lämnar ej-Done. F09-cleanup är ett separat explicit anrop efter Done med verklig F13-inaktivitetsprobe; retention behåller worktree tills sådant anrop, branches och all historik bevaras. Ingen TeamPlayer-produktadapter föreE06, main-merge eller långlivad Integration Agent. Verkligt Git/SQLite/test/MCP med kontrollerad runtime; samlat native flöde iF22 och F17-autonomigate införF38 kvarstår.

**Worker F-21 READY_FOR_REVIEW (2026-10-08):** task/e05-f21. Tre approval/delivery/reopen/cleanup/test-retry-prov (54.61 s), tio unknown-test/exit-loss/changed-proof/runtime-prov (273.48 s), 32 F13/CLI-regressioner (81.82 s), två senaste ordnings-/exit-recovery-prov (30.68 s) passerar. Ruff/build/diff/CLI, dokumentlänkar och paketerad policy exit0. 13 tidigare serviceprov passerade före concurrencyreview-fix; simulerad global klocka fastnade i harness, ägda testprocesser stoppades (143) och verklig tresekundersgräns används. Konkurrerande retry kan ge begränsad DELIVERY_BUSY; samma nycklar återfinner exakt en merge/test/exit/Done. A1 verifierad --no-ff/current F20/F18/test/runtime-before-merge, faktisk eftertest-SHA och bekräftat stopp/slotrelease före Done. A2 test-/proof-/runtimefel bevarar merge/slot/worktree och ej-Done. A3 processförlust efter Git, test och fysisk exit samt omstart/idempotens återanvänder resurser; explicit ny testnyckel återverifierar samma merge. Riktig Git/SQLite/testprocess/MCP; runtime/processer kontrollerade, verkligt samlat native flöde F22 kvarstår. F09-retention och faktiskt kopplad inaktivitetsprobe för explicit cleanup; F17-autonomigate införF38 består. [Kontrakt/recovery](docs/review/F-21-leverans.md). Aktuell review, bootstrap --no-ff och full faktisk merged regression återstår före Done.

**Verifierad leverans F-21 (2026-10-08):** F-21 APPROVED 9a064d092eb7b9401bce9abe93ca57d018b55f5e mot f808c97c7314ab169c068fd00ca29606c262ab5c; full diff 74451 bytes/SHA256 2e04501004371df3790c1cb24381e90bcd741f65f88dc996e58c9bd24da4ed8c; faktisk --no-ff Task→Epic 4486d108a3dbfed53f9e0ef38430f19e315a905f, operation ba09fa2a-dbe5-4e95-b735-8ae31e607025, exakta parents och identisk granskad tree. Tre current delivery/reopen/cleanup/retry-prov (54.61 s), tio negative/recovery/runtime-prov (273.48 s), 32 F13/CLI-prov (81.82 s) och två senaste ordering/physical-exit-recovery-prov (30.68 s). På faktisk merge: 469 passed (957.77 s), exit0; Ruff/build/diff/CLI och paketerad policy/dokumentlänkar exit0. A1: slutförd aktuell F20/F18 approval/context/test/authenticated principal och faktisk idle/unik slot före kritisk --no-ff, självständigt merge-SHA-test och bekräftat fysiskt generationsbundet stopp före slotrelease/Done. A2: eftertestfel/okänt testutfall/ändrade proof/HEAD och kvarvarande processer bevarar faktisk SHA, slot/worktree/historik och ej-Done; ingen cleanup på osäkrat arbete. A3: crash efter Git/test/physical exit och omstart återanvänder merge/test/session; failed/unknown test kräver explicit ny verification_key för omprov på samma merge. Exklusiv epicleverans, current config/context digest och kontrollerat concurrent Busy/retry. F09 explicit cleanup med faktisk F13-inaktivitetsprobe bevarar branch/run/session/review/mergehistorik; default behåll worktree. Testharness globala clock-loop och bounded-lock-race dokumenterade/korrigerade; inga produktkrav kringgås. Verkligt Git/SQLite/testprocess/MCP, kontrollerad runtime/processfixture; samlat native flöde F22 och F17-autonomigate införF38 kvarstår. Bootstrap Integration/GitAdapter-undantag utan fabricerade produktruns. E05 Active, egen0/3 tills F22/slutreview/main-integration. Första fulla merged grinden: 468 passed/1 failed (1079.68 s), befintligt concurrent Worker-report-prov REPORT_UNVERIFIED utan fångad rotorsak. Exakt samma SHA: isolerat diagnostikprov 1 passed (11.01 s), därefter hela grinden återkörd med diagnostik till resultatet ovan. Ingen kod/acceptans/testgräns eller merge ändrades mellan dessa grindar; rotorsaken till första intermittenta felet är inte bevisad. Se [reviewunderlag](docs/reviews/F-21.md). TeamPlayer Done återläst med korrekt User-tilldelning.

### Task F-22 Verifiera review och fix till integrerad task

**Epic/fas/prioritet:** E-05 / 5 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `875b4e3d-e3de-40ae-bb76-6aee5e8705c2`.

**Körbar:** Ja — F-21 verifierad/Done på epic, E-04 Done på main och X-01 verifierad för separat namngiven native testserver/ofarligt repository. F17-autonomigate införF38 kvarstår.

**Källa:** A §§20–22, 39; W §§19–26. **Berör:** Verkligt testflöde, dokumentation.

**Beroenden:** F-21. **Externa förutsättningar:** X-01 och en liten testtask vars acceptansfel kan verifieras oberoende.

**Arbetsinstruktion för Codex:** Kör en liten verklig task med ett avsiktligt tydligt acceptansfel, begär korrigering, granska på nytt och integrera. Dokumentera taskhistorik, båda reviews, testkommandon och merge-SHA. Prova också en task med blockerande testfel.

**Resultat och kontrakt:** Samma Worker används för korrigeringen. Reviewen körs via Integration-policy från F-19; den fullständiga långlivade epicruntime kommer i E-09. Main-merge ingår inte i detta prov.

**Acceptans**

- [x] **F-22.A1:** Första review ger konkret CHANGES_REQUESTED och andra review godkänner den korrigerade committen.
- [x] **F-22.A2:** Tasken når Done med rätt merge och testunderlag; main är oförändrad.
- [x] **F-22.A3:** Blockerande testfel hindrar merge/Done enligt det steg där felet uppstår.

**Verifiering:** Verkligt Herdr/Codex-prov i ofarligt repository med sanerad review- och commitrapport.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start/kontrakt F-22 (2026-10-08):** F21 Donev7 efter faktisk merge4486d108 och full omgrind469pass957.77s; E04 Done på main. Write/get_me/User-ägare verifierade. Bootstrap task/e05-f22 från aktuell epic77b43c8322b5e9b56652505abcb6d6449c6237a1. Första add_worktree-anropet saknade explicit branch_exists och gav TypeError utan sidoeffekt; taskstatus plockades innan branchförberedelsen slutförts. Detta ordningsfel avstämdes direkt: nytt korrekt GitAdapter-anrop skapade/verifierade ren branch/path/bas före någon kodändring. Ingen produkt-run/ägarskap fabriceras.

Provet använder användargodkänt ofarligt repo, separata f22-probe/f22-epic/run/task-ID:n och namngiven server hc-f22-20261008, högst en fysisk Worker. En avsiktlig granskningsdraft normaliserar med overridebar str.split och redovisar den kända begränsningen; faktisk oberoende subclass-regression reproducerar acceptansfelet. READY_FOR_REVIEW är reviewöverlämning, ingen falsk fullacceptans eller approval. Integration följer paketerad F19-policy, konkret negativ review genom F19, samma-session-ACK, korrigerad native rapport och F18 nytt fullständigt context/test; F20 positiv review endast för alla faktiskt verifierade kriterier, F21 faktisk merge/eftertest/fysiskt stopp före Done. Separat native task efter frigjord slot möter operatörens fördeklarerade oberoende testfel, vilket ska hindra READY/delivery/Done; dess runtime stoppas säkert och historiken bevaras. Produktservices används för alla fixture-kritiska operationer. Main-SHA kontrolleras före/efter; ingen fixture-main-merge. Retention behåller worktrees/branches/state/full privat nativejournal; sanerat spårbart underlag i docs/review. Native Workers behöver inte direkt anropa MCP i detta manuellt drivna fas5-prov; långlivad Integration-session finns först i E09 och F17-autonomigate införF38 kvarstår.

**Worker F-22 READY_FOR_REVIEW (2026-10-08):** task/e05-f22. Verkligt Herdr0.9.3/Codex i godkänt ofarligt repo, en Worker åt gången. Draft9a7bc990 mot epicbas e90b68ce: oberoende subclass-regression exit1, konkret CHANGES_REQUESTED e9826cf2 och korrelerad ACK i samma session01a11c29-16f7-73f1-a963-a45ffdb22ad3. Korrigerad009f3dd0/tio tester/regressionexit0, ny native report/context1a62e963 och full granskning; APPROVED f7533833. F21 faktisk --no-ff merge77abb664, självständigt merge-test d00fb45d exit0 och fysiskt stopp e16b9614 före slotrelease/Done. ReplayEXISTING, exakt en merge. Separat task14faff65 source54ec8302: verkligt independenttest b2063920 exit23/FAILED/TEST_FAILED, rapportnekad och same-key återläsning utan extra test, ingen READY/review/merge/Done. F13stopp02f8055c lämnarPARKED/Attention utan slot med bevarade resurser. Main före/efter7ce621ad oförändrad. Båda Workers bekräftat inaktiva, endast ägd namngiven testserver stoppad. Harness syntax/lint och evidensexport TypeError (get_operations saknatkind) korrigerade; produktutfallet redan journalfört, inga produkter/acceptanser kringgådda. Guard nekar verkliga utvecklingsrepot före sidoeffekt; Ruff/build/diff/CLI/dokumentlänkar/evidenskonsistens exit0. [Återkörning och gränser](docs/review/F-22-native.md), [sanerad evidens](docs/review/F-22-prover.json). Full privat journal behålls i ignorerad probe; ingen cleanup. Operatörens Integration driver produktservices, ingen långlivad Integration Agent eller native Worker-MCP-kall; F17-autonomigate införF38 kvarstår. Egen bootstrap review, Task→Epic-merge och integrationens grindar återstår före F22Done.

**Verifierad leverans F-22 (2026-10-08):** F22 APPROVED 7f5b148a81af00a67de462cb1c147cf67e8c9635 mot 77b43c8322b5e9b56652505abcb6d6449c6237a1; full diff 50528bytes/SHA256 c6a8a789629e7d72354257c7b721ae03e1de3772e502c685367810e8392941c7, operation c6af9a03-fea1-47dc-8557-3a502f545531. Faktisk bootstrap Integration/GitAdapter --no-ff Task→Epic 944f86586ca147a19c1667a0e05d9ddc7936e6a1, exakta parents och identisk granskad tree, utan fabricerade produktruns. På faktisk merge:10CLItester passerade2.50s, Ruff/build/diff/CLI/fixtureguard/dokumentlänkar och public/nativejournal-identity/verifierhash/evidenskonsistens exit0. NativeA1: initial9a7bc990→corrected009f3dd0, regression1→0, tio tester, sammaSID01a11c29-16f7-73f1-a963-a45ffdb22ad3 och reviewsCHANGES_REQUESTED/APPROVED. A2: faktisk produktfixture-merge77abb664/posttest0/physicalstop före Done, replayEXISTING/enmerge; main7ce621ad oförändrad. A3: separat verklig Worker/source54ec8302, independenttestexit23/FAILED, rapportnekad/ingenReady/merge/Done; återläsning utan extra test, säkertPARKED utan slot/historikförlust. BådaWorkersfysisktinaktiva och endast ägd testserverstoppad. Applikationskod/tester/beroenden oförändrade sedan F21:s fulla actualmergegrind469pass957.77s; F22egenverifiering är nativeprov/harness/dokumentation. E05egenacceptans3/3 från nativekedjan och tidigare versions-/felprov, förblirActive tills samlad verifiering/slutreview/PR/mainmerge. Ingen långlivadIntegration/nativeWorkerMCP-claim; F17autonomigate införF38 kvarstår. TeamPlayer Done återläst med korrekt User-tilldelning. [Slutreview](docs/reviews/F-22.md).

**Coordinator/verifierad slutleverans E-05 (2026-10-08):** Coordinator E05 APPROVED 4a046ea7b77319629eba5187063dc6c0fde77064 mot a0937fa70614df5ce0006cb60b4caaead6c5fe8c; komplett diff 316746bytes/SHA256 a7efbffc20031a06a01fe4989344e3a748517b3a5240b5ea8dd9cb08892f7f7a, operation b738935e-9e91-4e18-93f2-87b932f7ad00,43filer. Samlad epicgrind469pass681.55s och Ruff/build/diff/CLI/dokumentlänkar/exakta paketeradepolicyer exit0. PR https://github.com/johanLstang/HerdrCoordinator/pull/5, faktisk GitAdapter-bootstrap --no-ff mainmerge 9d16c7fd41825fc18927fcc00fca9e4138c1cab8, exakta parents(a0937fa70614df5ce0006cb60b4caaead6c5fe8c,4a046ea7b77319629eba5187063dc6c0fde77064) och identisk granskad tree; ordinaryfast-forwardpush utanforce, remoteHEAD och PRmerged/closed/exaktmergeSHA återlästa. På faktisk main: 469 passed (684.75s), exit0; övriga grindar/policypaket/länkar/rättimportkälla/rentworktree passerar. AllaF18–F22Done med färsk nativeUser/tilldelningsavstämning; egenacceptans3/3. A1 nativeF22CHANGES_REQUESTED→samma-sessionACK→korrigerad009f3dd0→färsktcontext/test→APPROVED. A2 nativeTaskEpic77abb664/posttest0/currentGenerationphysicalexit→slotrelease→Done, replayEXISTING/ingenextramerge/mainfixture7ce621adoförändrad. A3 separat nativeindependenttest23 nekadReady/merge/Done och säkertPARKED, samt F18–F21 stale/context/config/role/unknown/test/exit/recovery-prov. NativeWorkersfysisktinaktiva/testserverstoppad, retention bevarar fixturebranches/worktrees/session/state; ingencleanup. Ingen schemamigration; operatorIntegration föreE09, ingen directnativeWorkerMCP-claim. F17autonomigate införF38 består. FörstaF21fullgrind468pass/1intermittentWorkerReportfel1079.68s/okändrotorsak; isolerat1pass11.01s +sammaSHAfull469pass957.77s utan kod/testgräns/mergeändring; nu även aktuellepicfull469pass681.55s. Samtliga utfall sparade ärligt. BootstrapGitAdapterundantag utanfabriceratproduktägarskap. TeamPlayerepicDone och beskrivning återlästa; E06/F23 nästa prioriterade kandidat från verifieradmain. [Slutreview](docs/reviews/E-05.md). Tidigare PR/main-väntan är avslutad historik.

## Epic E-06 Spegla arbetsflödet i TeamPlayer

**Fas:** 6. **Prioritet:** P1. **Kanban-status:** Done. **TeamPlayer Epic-ID:** `e779c93c-7f75-43e0-97ed-53373bb0fd66`.

**Körbar:** Nej — samtliga F23–F26 granskade/Done; E06 slutgranskad, mergad och slutverifierad på main. **Beroende:** E-05 Done på main.

**Källa:** A §§6, 11, 16, 23, 40; W §§5, 9, 13–18, 39.

### Resultat och omfattning

Operatören kan välja arbete från TeamPlayer och se rätt arbetsstatus, blockerare och verifieringsreferenser utan att tekniska runs går förlorade.

**Ingår:** Verifierat MCP-adapterkontrakt, projekt/epic/task-läsning, beroenden, statusskrivning och återförsökbar synk. **Utanför:** Full scheduling, parkering och automatiskt val av flera epics.

**Gemensamma regler:** tillämpa samtliga sex kontrakt ovan och de ingående taskernas förvillkor. **Risk och beslutspunkt:** Externa schema- och statusnamn ska verifieras i F-23. TeamPlayer och SQLite/Git saknar gemensam transaktion; F-25 behöver beständiga väntande skrivningar.

### Tasks och beroenden

| Task | Leverans | Taskberoenden utöver epicens beroende | Prioritet |
| --- | --- | --- | --- |
| F-23 | Verifiera TeamPlayer projekt och MCP kontrakt | E-05 | P1 |
| F-24 | Läs epics tasks och beroenden till domänmodellen | F-23 | P1 |
| F-25 | Synkronisera status och kommentarer utan nya sidoeffekter | F-24 | P0 |
| F-26 | Verifiera TeamPlayer kopplingen på en testepic | F-25 | P1 |

### Epicacceptans

- [x] **E-06.A1:** En testepic och dess tasks kan läsas med riktiga ID:n, kriterier och beroenden.
- [x] **E-06.A2:** Tasks får Active/Attention/Done och epics Planned/Active/Done på verifierade domänhändelser med rätt ansvarig roll; påbörjade epics behåller Active fram till verifierad main-leverans.
- [x] **E-06.A3:** Avbruten TeamPlayer-skrivning återförsöks utan att start, merge eller kommentar dupliceras.

Epicen följer dessutom den gemensamma definitionen av Done. Acceptansen verifieras genom taskernas underlag och ett samlat prov av epicens resultat.

### Task F-23 Verifiera TeamPlayer projekt och MCP kontrakt

**Epic/fas/prioritet:** E-06 / 6 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `6dd3f8a2-cfcf-4483-a124-944804a5f65f`.

**Körbar:** Nej — levererad; E05 Done/main9d16c7f +469sluttester, aktuellmainmetadata84fb77a; X02 verifierad: Write/get_me och separat testepic8051e9de-f4dc-4277-8f80-62b1c36c4690.

**Källa:** A §§6, 40; W §§5, 9, 13, 39. **Berör:** TeamPlayer-adapterkontrakt, testprojekt.

**Beroenden:** E-05. **Externa förutsättningar:** X-02: TeamPlayer MCP och en tillgänglig testepic med avsedd behörighet.

**Arbetsinstruktion för Codex:** Verifiera MCP-anslutning, projektidentitet, epic/task-schema, acceptansfält, beroenden, statusar, kommentaroperationer och tillgängliga create/reopen-operationer inför epickorrigering. Dokumentera exakta verktygsnamn och konton/roller som adapterkonfiguration; anta ingen utförare från den äldre projektguiden.

**Resultat och kontrakt:** Börja i en särskild testepic/testmiljö. Verifiera eventuell pagination, taskhierarki och hur Planned/Active/Attention/Done representeras. Separera läsbehörighet från skrivrättigheter och förvara credentials externt.

**Acceptans**

- [x] **F-23.A1:** Rätt testprojekt och epic identifieras med verifierade externa ID:n.
- [x] **F-23.A2:** Varje nödvändig läs/skrivoperation har verifierat schema och behörighet eller konkret blockerare.
- [x] **F-23.A3:** Acceptans och beroenden kan återges utan att data tappas eller credentials loggas.

**Verifiering:** Avgränsade verkliga MCP-prov mot testepic med sanerade request/resultatexempel.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start F-23 (2026-10-08):** alla fem F18–F22beroenden Done, E05 Donev17/PR#5/main9d16c7f/469sluttester684.75s; currentmainmetadata84fb77a. Write/get_me/User-ägare verifierade, ren task/e06-f23 från aktuell epic06388a5 före nativeplock Pending→InProgressv2. E06 Active, separat fixture8051e9de-f4dc-4277-8f80-62b1c36c4690 återläst. Actual MCP-katalog/anslutning/schema/behörighet/projekt och fixture-prover utförs; ingen produkttransport/scheduling eller statussynkservice före F24/F25. Alla fixturetasks tilldelas verifierad User105f26a7-0648-438d-94fd-3260ac3af4ee; inga andra projekt ändras, IDs/historik bevaras, inga credentialvärden i underlag.

**Worker READY_FOR_REVIEW (2026-10-08):** verklig MCP/SDK2.3.0/protokoll2025-11-25, full16verktygskatalog, fixture8051 med två Usertasks/förlustfriUnicode/beroende. StatusCAS/stale-rejection, idempotent create/repeat/noextraevent, epicstatus/description/readback samt missingtask/wrongproject avvisade. Native kommentar/delete/reopen/dependency-edit saknas; beskrivningshändelser explicit kontrakt inför F25, inga produktservices eller falseDone. Ruff/build/diff/CLI/länkar/JSON och actualverify/fixturereplay exit0. Integrationreview/merge/gate återstår; underlag [F23](docs/teamplayer/F-23-kontrakt.md) och [proof](docs/teamplayer/F-23-prover.json).

**Integration Done (2026-10-08):** review8bc25170006902d14d0989f7fff48d5bce8fdded mot06388a5cd8d64b8b6c978cef6fc3872eb9571696; full53701bytes/SHA2563d6f65217d8f891720cd33ed4c8cb3f6079b589d31ce716c23e03c919977e911. Faktisk GitAdapter--no-ff173b7f6fbe80dc19278e60add640962af63ba12f/exaktparents/tree. Actualmerge23startup/CLItests8.24s +Ruff/build/diff/CLI/länkar/JSON/native-readback exit0. NativeDonev5, acceptans3/3; [review](docs/reviews/F-23.md). Inga produktservices föreF24/F25, E06Active/egen0av3.

### Task F-24 Läs epics tasks och beroenden till domänmodellen

**Epic/fas/prioritet:** E-06 / 6 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `7a9a311f-5f20-4247-9ef7-a5e5c57e39bc`.

**Körbar:** Nej — levererad; F23 Done/taskmerge173b7f6 och verifierat läskontrakt/X02; E05 Done på main.

**Källa:** A §§6, 18, 40; W §§5, 9, 28. **Berör:** TeamPlayer-adapter, taskinmatning, externa ID-kopplingar.

**Beroenden:** F-23. **Externa förutsättningar:** X-02 och verifierat läskontrakt från F-23.

**Arbetsinstruktion för Codex:** Implementera get_epic, get_epic_tasks, get_task_dependencies och läsning av kandidater för senare epicval. Mappa externa fält till validerade domänobjekt och bevara prioritet, acceptans, källor och lokala ID-kopplingar.

**Resultat och kontrakt:** TeamPlayer är källa för arbetsstatus; SQLite behåller runtime. Alla sidor hämtas enligt verifierat API. Fel projektkoppling, saknad acceptans, okänt beroende eller cykel gör berört arbete ej körbart med konkret orsak. Läsning startar inga Workers.

**Acceptans**

- [x] **F-24.A1:** En testepic med flera tasks återges komplett med prioriteringar, acceptans och beroenden.
- [x] **F-24.A2:** Pagination och upprepad läsning tappar eller duplicerar inga tasks.
- [x] **F-24.A3:** Fel projekt, beroendecykel eller borttagen task avvisas/flaggar berört arbete utan automatisk start.

**Verifiering:** Adapterprov med kända sidresultat och felaktig graf samt verklig läsning av testepicen.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start F24 (2026-10-08):** User/Write och samtliga6taskberoendenDone verifierade, E05Donepåmain/F23Done+review173b7f6; ren task/e06-f24 från aktuellren epic64c2e03. Native Pending→InProgressv2 före kod. E06 Active. Endast läsadapter/validerad graf, inga Workers/start/statuswrites.

**Worker READY_FOR_REVIEW (2026-10-08):** frysta externa boardmodeller/explicit local-ID/sourcebindning, read-only MCPtransport/provider, fullgraf/repeatedreads/graphissues; ingen runtime/state/statuswrite. 113relevanta tester14.40s inkl riktig in-processMCP/tvåkatalogsidor och negativa fall. Riktig produktadapter13epics/52tasks/nativefixture2tasks/lossless/binding/listget/repeat, dependencyNotDone korrekt. Ruff/build/diff/nativeexit0; [läsning](docs/teamplayer/F-24-lasning.md)/[proof](docs/teamplayer/F-24-prover.json). Taskintegration/mergegrind återstår.

**Integration Done (2026-10-08):** review4692b8e3b9099992fa80d110b944b1ebc6289990 mot64c2e0303c992a8cf7de8a36fbd9e096d7abf92d; full60072bytes/SHA256ae31706b8c9c46dfe1af447632f1fcbaf142f5a97058f2b7d7fb61b945c4ea49. Faktisk GitAdapter--no-ff eef679e5996209c774faa57d18a9e9c4beebde9d, exaktparents/tree. Actualmerge113tests14.01s +Ruff/build/diff/CLI/länkar/package/native/publicverify exit0. NativeDonev3 eftergrind, acceptans3/3; [review](docs/reviews/F-24.md). E06egenA1verifieradviaProduktReader, Active1av3; F25nästa.

### Task F-25 Synkronisera status och kommentarer utan nya sidoeffekter

**Epic/fas/prioritet:** E-06 / 6 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `7ab4a0ac-405d-4905-bc46-a2d5f0431c86`.

**Körbar:** Nej — verifierad och integrerad till E06; ingen återstart.

**Källa:** A §§6, 16, 23, 30, 40; W §§15–18, 24–25, 39–40. **Berör:** TeamPlayer-adapter, väntande skrivningar, statuspolicy.

**Beroenden:** F-24. **Externa förutsättningar:** X-02 och skrivkontrakt från F-23; avstämningsstöd måste vara verifierat.

**Arbetsinstruktion för Codex:** Implementera rollstyrd set_task_status, set_epic_status och kommentarer med beständiga synkavsikter. Tasks får Active efter startbekräftelse, Attention vid verifierad blockerare och Done efter taskmerge/verifiering. Epics får Planned före start, Active vid Coordinator-start och genom review/hinder/väntan på main-integration, samt Done först efter samlad acceptans/slutreview/main-merge/slutverifiering. Använd update_epic_status med färsk list_epics-version och mappa Pending/InProgress/Done till Planned/Active/Done. Spara retryläge och referens till domänhändelsen.

**Verifierat MCP-kontrakt F23:** saknat fristående kommentarverktyg; logiska kommentarer blir markerade beskrivningshändelser via update_task_details med färsk get_task-version, stabilt event-ID/innehållsdigest och readback. Bevara manuell text; okänt/avvikande utfall kräver avstämning. Ingen native kommentar- eller reopen-kapabilitet fabriceras. Se [F23-kontrakt](docs/teamplayer/F-23-kontrakt.md).

**Resultat och kontrakt:** Integration initierar taskskrivning och Coordinator epicskrivning. Externt fel efter lokal framgång återförsöker endast TeamPlayer-steget. Okänt kommentarutfall avstäms med verifierad dedupliceringsmekanism; om API saknar den används dokumenterad readback eller Attention. Manuellt ändrad Kanban ger avvikelse, inte bevis för merge. Epic-Done kräver dessutom explicit SHA-bunden komplett checklista via register_epic_review(verified_criteria=...), aktuellt verifierat scope och native tasks Done; äldre review utan checklista räcker inte. F40 använder detta reviewkontrakt vid Coordinatorns slutreview.

**Acceptans**

- [x] **F-25.A1:** WORKING, BLOCKED/PARKED och DONE ger rätt taskstatus med orsak eller merge/testreferens; epicen är Planned före start, Active under arbete/review/hinder och väntan på main-integration, och Done först efter verifierad main-merge med samlad acceptans/slutreview.
- [x] **F-25.A2:** Nätfel efter lokal merge ger väntande synk och återförsök utan ny merge.
- [x] **F-25.A3:** Worker kan inte skriva status och upprepad samma kommentarhändelse ger inte flera identiska kommentarer.

**Verifiering:** Adapterprov med fel före/efter externt svar samt verkliga statusskrivningar i testepicen.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Start F25 (2026-10-08):** rättWrite/User och samtliga6taskberoendenDone verifierade, E05Donepåmain/F24Done+revieweef679e/113tests14.01s/nativeReaderPASS. Ren task/e06-f25 från renaktuell epice235a21 före nativePending→InProgressv5. E06 Active/egen1av3. Worker implementerar beständig rollbunden status/history-sync enligt verifieratF23kontrakt; inga privata credentialvärden eller fabricerade Git/runtimebevis.

**Worker-verifiering F25 (2026-10-08):** 91 sync/writer/MCP/startup-test passerade291.33s; separat129 reader/writer/persistence/state/epicintegration passerade104.95s; efter skärpt scopekontroll6 epicacceptansfall passerade51.67s. Ruff/build/configCLI/diff/länkar/paketerade moduler PASS. Native fixture63cebf13: faktiskF22-backup, task2eabe2f5 Donev3/taskcfe70b9f NeedsInputv3/epicActivev4, injicerat förlorat klientsvar efter riktig Donewrite→Pending→readback→en historikhändelse, oförändrad Git/runtimejournal. Återläsning/replay PASS; inget nytt Worker-start/merge. Tre första testfixturfel och en ruff-borttagen fixtureimport samt fel förväntad F05-resultattyp rättades utan sänkta grindar; senaste kontroller gröna. Sourceleverans väntar Integration-review/Task→Epic och postmergegate; lokal och TeamPlayer-task fortsattActive. [API och avstämning](docs/teamplayer/F-25-synk.md), [native underlag](docs/teamplayer/F-25-prover.json).

**Integration/fix F25 (2026-10-08):** första --no-ff Task→Epic3fe0568aacc7d1fe1b5908b91b57d0c7384502c7, review6f851a4→e235a21/full111799bytes/digest199822c5;204tests367.53s samt native via faktiskEpicmodul/Ruff/build/CLI/diff/links/package PASS. Granskning hittade F13:s legitima stoppalias: tasken behåller Active medan exakt park-/leveransstop-ID införs och omverifieras. Integration synk Epic→Task7bc3525, inga fabricerade runbevis eller upprepningar av tidigare merge. Workerfixens12 berörda test passerade82.34s, nativeåterläsning samt Ruff/build PASS. Ny fixreview/merge och berörda postmergegrindar återstår före Done.

**Slutleverans F25 (2026-10-08):** source589631efc4f16b3360d5749bdde9f60ac82547bf mot aktuellEpic3fe0568aacc7d1fe1b5908b91b57d0c7384502c7; fixdiff5882bytes/SHA25626f0d38870b218c80f06ea1fa233637192d4d8fa02937d886ae323424b4aeff2. Godkänd Integration-review och faktisk --no-ff Task→Epicde46d0febca95519d32526085dba1f67c235e4ae med exakta parents och granskad tree. Ursprunglig full14fildiff111799bytes/199822c5 och Taskmerge3fe bevaras; särskild Epic→Task-synk7bc före aliasfix. Faktisk slutmerge12 relevanta tests83.58s samt native produktmodul/Ruff/build/configCLI/diff/länkar/paketerad stop-ID-grind PASS; ursprunglig merge204tests367.53s. NativeF25 Donev6 efter dessa grindar. F25A1–A3 uppfyllda; E06A3 verifierad men A2 återstår för F26:s kompletta liveflöde. E06 fortsattActive/egen2av3. [Granskning och SHAs](docs/reviews/F-25.md).

### Task F-26 Verifiera TeamPlayer kopplingen på en testepic

**Epic/fas/prioritet:** E-06 / 6 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `1626d7a9-387d-47cd-b20b-86cb2a9f0613`.

**Körbar:** Nej — granskad, integrerad och verifierad. NativeDone efter Task→Epic6d6ebd9 och efterkontroller; trust/retry är bevarad historik.

**Källa:** A §§6, 16, 40; W §§13–18, 25, 39. **Berör:** Kanban-prov, dokumentation, ID-mappning.

**Beroenden:** F-25. **Externa förutsättningar:** X-01 och X-02; särskild testepic med tillåtna statusskrivningar.

**Arbetsinstruktion för Codex:** Kör läsning, bekräftad Worker-start, blockerarrapport och verifierad taskintegration mot testepicen. Prova bortkoppling under en statusskrivning och återställ synk. Dokumentera aktuella projekt/epic/task-ID och återstående blockerare.

**Resultat och kontrakt:** Attention-skrivning, manuellt tillförd provinput och bekräftad fortsättning verifieras här. Bootstrap-operatören använder redan levererad F13-park/resume och lämnar ett beständigt avgränsat inputmeddelande i samma session; automatisk Attention-policy/input-loop kommer i E-08/F43. Testepicen ska särskiljas från projektets produktbacklogg. Runtime och TeamPlayer avstäms utan att Kanban ensam flyttar Git eller runs.

**Acceptans**

- [x] **F-26.A1:** Testtaskens Kanban följer Planned → Active → Attention, manuellt tillförd input och bekräftad fortsättning till Active samt verifierad leverans till Done med rätt underlag.
- [x] **F-26.A2:** Nätfel lämnar synkavsikt som kan slutföras efter återanslutning.
- [x] **F-26.A3:** Ingen annan epic/task ändras och inga credentials hamnar i rapporten.

**Verifiering:** Verkligt MCP-prov med ofarlig testtask, kontrollerad bortkoppling och före/efter-läsning.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Coordinator/start E-06 (2026-10-08):** från ren verifierad main84fb77a3141cae25710554b497b8705f7768cbe8 efter E05 PR#5/main9d16c7f/469pass684.75s och Donev17/descriptionmatch. Ren feature/epic-e06 i eget worktree, NativeE06 InProgress/Activev7. Write/get_me verifierar HerdrCoordinator och Userblitterbot@gmail.com. X02 separat namngiven [TEST]epic8051e9de-f4dc-4277-8f80-62b1c36c4690 skapad Pending med unik idempotencyKeyherdr-coordinator-e06-mcp-probe-20261008 och återläst. Fixtureepic är externa kontraktsprov/testdata, ingen implementations-/produktrun eller förtäckt produkt-Done; alla skapade testtasks får verifiedUser105f26a7-0648-438d-94fd-3260ac3af4ee. Inga andra projekt ändras, IDs/historik bevaras. F23 första kandidat efter X02; lokala credentialvärden har inte skrivits till dokument/logg/prompt.

**Start F26 (2026-10-08):** list_projects/get_me verifierar HerdrCoordinator/Write/User105f26a7 (blitterbot@gmail.com); list_epics/list_tasks visar14epics/54tasks, inga tidigare egna pågående implementationstasks, E06Activev12/egen2av3 och samtliga6native beroendenDone. F25review589631e/slutmergede46d0f/final12tests83.58s/nativeprodukt/Ruff/build PASS, NativeDonev7 med exaktbeskrivning. Task/e06-f26 i separat worktree från ren aktuell epic90d2d3c810d45957e6900ed287324525d54ded45. Native Pendingv1→InProgressv2 och återläst User-tilldelning före kod/runtimearbete. Första bootstrap-preparationskommandot kördes felaktigt utan uv och saknade paketimport; claim fortsatte därför före lyckad worktree-preparation. Avvikelsen rättades direkt med uv/GitAdapter, ren branch/bas verifierades och tasken återlästes innan någon kod/runtime ändrades. Ingen beroendeordning eller ownership ersattes. Färsk isolerad fixture bevarar alla äldre prov; en Worker, inga parallella backloggfeatures.

**Verkligt hinder F26 (2026-10-08):** native Codex visar Folder access/Trust and continue för den nya ofarliga fixturesökvägen `.worktrees/task-e06-f26/.herdr/probes/f26/repo`, skild från tidigare betrodd F10-fixture. Herdr-skillen kräver användarbeslut före svar på faktisk trust-/approval-UI. Fråga skickad asynkront, inget svar har automatiskt valts. Runtime STARTING/runf5b1db25-7a1a-4baa-b69c-dad545c507b1/start4dc1c848/PENDING/RUNTIME_BLOCKED, agenthc-4dc1c8486db84b23a587425a/panew1:p1 på egen serverhc-f26-20261008. Slot1 hålls; inga ACK/session/prompt/kod/merge ännu. NativeF26 NeedsApprovalv4; E06Active. Fixtureepicf74e4cbb/task88460303 är separata test-ID:n; fixturetaskPending, ingen fabricerad Active/Attention. Nästa ansvariga roll testoperatör: efter användarbeslut besvara exakt dialog och avstäm samma start/runtime innan återförsök. Ingen senare beroende feature väljs.

**Bevarat arbetsläge F26 (2026-10-08):** harness [scripts/probes/f26_native.py](scripts/probes/f26_native.py) och [liveprov/återupptagning](docs/teamplayer/F-26-liveprov.md) är förberedda. Ruff och diffkontroll PASS; inga F26-kriterier markeras uppfyllda. Verklig fixtureepic Pendingv2→InProgressv3 via levererad synkservice/op4a9485c3 medan fixturetask fortfarande saknar ACK och ligger Pending. Skrivgräns/journal begränsar produktprovets statusskrivningar till fixtureepic/task-ID. Före/efter-avstämningen redovisar F26-utvecklingstaskens separata workflowändringar i status/version/beskrivning; harnessen får inte skriva den tasken. Övriga boardobjekt och dess övriga fält ska förbli oförändrade. F26 fortfarande Attention/NeedsApproval, E06Active/egen2av3; full livekedja och återanslutning återstår efter beslut.

**Återupptagning F26 (2026-10-09):** användaren godkände uttryckligen trust för samma F26-reporot; NativeF26 NeedsApprovalv6→InProgressv7. get_me/list_projects och board15epics/55tasks verifierar samma User/Write, E06Activev12 och samtliga6beroendenDone. Egen testserver var stoppad sedan föregående körning; omstart av endast hc-f26-20261008 återställde tom shell i samma pane men nytt terminal-IDterm_65d64c10f5d481. Avstämning av samma start avvisades korrekt (RUNTIME_PANE_CHANGED); ingen agent/assignment/ACK/session finns. Gamla databasen/journaler/slot bevaras som misslyckat startförsök, ingen bindning fabriceras och ingen recoveryservice från F47 behandlas som levererad. Testoperatören kör därför ett nytt isolerat SQLite/F05-prov under f26-r2 mot samma oförändrade och godkända Git-reporot, nya lokala run/branch-ID:n och samma externa testepic/task. Originalets boardbaslinje och startjournal bevaras. Ingen senare feature eller samtidig Worker startas; detta ändrar inte produktens start-/recoverykontrakt.

**Verifierat nativeprov F26 (2026-10-09):** [liveprov](docs/teamplayer/F-26-liveprov.md)/[publicproof](docs/teamplayer/F-26-prover.json). Samma externa fixtureepicf74e4cbb/task88460303 och godkända reporot, nytt faktiskt F05-ägarskap efter avbrutet startförsök; run195d1ed1/session01a11ff3-8f54-7b70-893e-8a5a30f2f5e6 bevarad genom BLOCKED→fysisktPARKED→resume och ett enda inputmeddelande. Native taskPending→InProgress→NeedsInput→InProgress→Donev9; epicPending→InProgress→Donev4. Source079b6f25616c81c30b59ba685087d8a821cade40, aktuell fullreview3451bytes/SHA256cf3bd3328bbc43390e863eb4977c2fecfe0503f9dae5f2c88c855f326d0adccb/context36e6093b; oberoende9unittest+Unicode/type/empty på rapport/review/merge/aggregate/main PASS. Faktisk no-ffTaskmerge7197a73b183bd3613968a8fac534c20e8bd56413, posttest och fysiskstop28149470 före slotrelease/Done; replayEXISTING/enmerge/enresume. Faktiskt stängt MCP-transport gav beständig PENDING/WRITE_OUTCOME_UNKNOWN-op2f125089; återanslutning slutförde samma synk, fyra unika historikmarkörer exakt en gång. F08 aggregate/review/exakta parents/fixturmainmerged4215a26ba5e77d64a1fd4aed4117e5313c43563/finaltest PASS före testepicDone. Tio skrivförsök endast fixtureID:n; fullF24 före/efter jämför övriga boarddomänobjekt oförändrade/digest640d0743, separat dokumenterade status/version/textändringar på utvecklingstaskF26. Credentials hålls utanför underlaget. F26A1–A3 och E06A2 verifierade; E06 egen3/3 men fortsattActive inför samlad review/mainintegration. Utvecklings-F26 fortfarandeActive tills faktisk egen review/Task→Epic; ingen produktionsautonomi/recovery/F38claim. Ursprunglig avbruten run/slot/journaler bevaras; ingen låtsad park/Done.

**Utvecklingsverifiering F26 (2026-10-09):** uv run --locked pytest tests/test_startup.py:20PASS9.37s; Ruff/build/configCLI/diff/lokala dokumentlänkar PASS. Public credential-redaction-check mot aktuella privata MCP-header-/bearervärden:3filer PASS, inga värden skrivs ut. Separat /proc-cwd/paneprocess-avstämning visar endast återställd shell i ursprunglig avbruten worktree, ingen orphanCodex. Den kontrollen är även införd före framtida retryförberedelse; originaljournalen ändras inte retroaktivt. Nativeprovets fulla export passerar. För tidigt reviewförsök innan Worker-idle/READY avvisades; därefter färsk rapport/oberoende test och aktuellt context godkända. Inga grindar sänktes.

**Slutleverans F26 (2026-10-09):** source918bb1d8cd91426d02b1129ba8756cd48babe751 mot aktuellEpic90d2d3c810d45957e6900ed287324525d54ded45; full5fildiff64027bytes/SHA256089f656d5045e909f0b89fa9bab74599d70a77bb620f451dd1cda1592609b919. Integration APPROVED och faktisk bootstrapGitAdapter no-ffTask→Epic6d6ebd956b548df40c36e2ae89420c3f47c15965, exakta parents och granskad tree utan adopterade manuella produktruns. På merge20starttestsPASS6.98s samt Ruff/build/configCLI/diff och full nativeexport med faktisk Epicproduktmodul PASS; source20PASS9.37s/links/credentials PASS. F26A1–A3uppfyllda; NativeDonev9 med verifiedUser105. E06Active/egen3av3 inför samlad verifiering/PR/mainintegration. NativeWorker fysiskt inaktiv/agentlista tom och endast egen testserverhc-f26-20261008stoppad; samtliga gamla/newfixtures och historik bevaras. [Slutreview](docs/reviews/F-26.md).

**Coordinator/slutleverans E06 (2026-10-09):** aktuell APPROVED epic1fb14a47a62d42e5af8ba148e3b9318f26b521fd mot main84fb77a3141cae25710554b497b8705f7768cbe8; komplett34fildiff292847bytes/SHA256a335040866f1bb915e4a892dfd396a65a74cd5e44770a2a871c655f4559ae0b3/operation794a18b7-1c7a-4221-9273-39a5da16d5bf. Samlad uv run --locked pytest:588PASS1146.77s; Ruff/build/CLI/diff/exakta wheelmoduler/rollprompter/länkar PASS. PR https://github.com/johanLstang/HerdrCoordinator/pull/6; faktisk bootstrapGitAdapter no-ffMainmerge54e346da7fa2c9ff9eeb3856c8e64907b8bd6901 med exakta parents och identisk granskad tree. På faktisk Mainmerge205relevanta tester380.28s/exit0 för alla ändrade produktgränser/MCP/state/persistens/epicintegration/CLI samt Ruff/build/config/diff/wheel/länkar PASS; full588grind låg på identisk granskad tree före merge. Ordinarie push/remoteMain och PRmerged/closed/exaktmergeSHA återlästa. Alla4tasksNativeDone/verifiedUser105; egenacceptans3/3. NativeE06Donev14 återläst efter fulla leveransgrindar. Läs-/ägarskaps-/roll-/statusbevis, beständig outbox/CAS/readback/exakt-en-gång-historik samt native same-sessioninput/park/resume/task/epic-main/liveclosedtransport verifierade. Schema2/oförändrade beroendeversioner; långlivade loopar och F38OS-autonomigate är senare arbete. NativeWorkers inaktiva/egenF26testserverstoppad; avbruten originalstart/slot/journaler och lyckat prov bevaras utan fabricerad recovery. [Slutreview](docs/reviews/E-06.md). Nästa genomförbara feature F27 i E07 från verifierad aktuellmain.

## Epic E-07 Genomför beroendestyrda tasks med två Workers

**Fas:** 7. **Prioritet:** P1. **Kanban-status:** Done. **TeamPlayer Epic-ID:** `75285fe5-e9bb-46ea-b5fd-19135c40166d`.

**Körbar:** Nej — slutgranskad, mergad till main via PR #7 och slutverifierad Done. **Beroende:** E-06 Done på main.

**Källa:** A §§17–18, 29–30, 41, 48; W §§8–10, 23, 27–28.

### Resultat och omfattning

En epic kan genomföra minst tre tasks med högst två aktiva Workers utan manuell tilldelning och utan att beroenden startar för tidigt.

**Ingår:** Körbarhetsbedömning, slotreservation, scheduling, serialiserad integration och prov med tre tasks. **Utanför:** Flera samtidiga epics, fler än två Workers och dynamisk scaling.

**Gemensamma regler:** tillämpa samtliga sex kontrakt ovan och de ingående taskernas förvillkor. **Risk och beslutspunkt:** Kanban Active omfattar även review och får inte ensam användas som sloträknare. D-03 och F-28 definierar beständiga reservationer; F-29 serialiserar merge mot epicen.

### Tasks och beroenden

| Task | Leverans | Taskberoenden utöver epicens beroende | Prioritet |
| --- | --- | --- | --- |
| F-27 | Välj endast körbara tasks i rätt beroendeordning | E-06 | P0 |
| F-28 | Reservera högst två aktiva Worker slots | F-27 | P0 |
| F-29 | Driv scheduling och serialisera taskintegration | F-28 | P1 |
| F-30 | Verifiera tre tasks med två parallella Workers | F-29 | P1 |

### Epicacceptans

- [x] **E-07.A1:** Två oberoende tasks kan arbeta samtidigt och tredje task startar när en säker slot är ledig.
- [x] **E-07.A2:** Ett taskberoende blir körbart först efter granskad merge och godkända integrationskontroller.
- [x] **E-07.A3:** Samtidiga starter och integrationsförsök bryter inte workergräns eller review mot aktuell epic.

Epicen följer dessutom den gemensamma definitionen av Done. Acceptansen verifieras genom taskernas underlag och ett samlat prov av epicens resultat.

**Leveransläge (2026-10-09):** E07 Done efter PR #7, mainmerge `9ff5eb8698f2a0750d5eea3309a6ccb2d3c7aadd` och 119 sluttester PASS. Alla fyra tasks är Done med aktuella reviews och faktiska taskmerges. Egen acceptans 3/3; samlad verifiering täcker samtliga 692 aktuella testfall. [Epicens slutreview](docs/reviews/E-07.md).

### Task F-27 Välj endast körbara tasks i rätt beroendeordning

**Epic/fas/prioritet:** E-07 / 7 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `8c9e5322-28c7-4310-b444-4c3a843fb671`.

**Körbar:** Nej — granskad, integrerad och verifierad Done på E07.

**Källa:** A §§17–18, 41; W §§9, 27–28. **Berör:** Scheduler, beroendegraf, Git/runtimeunderlag.

**Beroenden:** E-06. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera task_get_next och beräkning av körbarhet från status, krav, externa förutsättningar, prioritet och beroenden. Använd stabil ordning vid lika prioritet och förklara varför varje task inte kan startas.

**Start/precisering F27 (2026-10-09):** E06 Done/main54e346d, mainbas ec28744 och native User105-tilldelning/beroenden verifierade. Coordinator startar E07 Active; Integration plockar F27 Active. Bootstrap GitAdapter skapade feature/epic-e07 och task/e07-f27 i separata worktrees från ec28744, utan fabricerade produkt-runs. Worker implementerar endast F27. task_get_next är läsande: betrodd operatör registrerar UUID→LocalBoardBinding, fullständiga LocalTaskSpec, stabil lokal taskordning och föregående epicberoenden. Boardtext blir aldrig en exekverbar specifikation. Samma-epic-beroenden kräver faktisk F21-delivery, registrerad aktuell approval/review, no-ff-merge, godkänt integrationstest och säkert stopp; tidigare epics kräver dessutom F08-mainmerge/sluttest och egen acceptans. Saknad explicit koppling/proof ger blockerare. Urval reserverar/startar inte, och start måste återvalidera aktuella förvillkor (F28/F29).

**Resultat och kontrakt:** Planned och komplett specifikation krävs. Föregående task ska vara granskad, integrerad och verifierad; Kanban Done ensam räcker inte om Gitunderlag saknas. Beroenden till tidigare epics kräver main-merge. Cykler, okända ID:n och Attention gör relevant kandidat ej körbar.

**Acceptans**

- [x] **F-27.A1:** Två oberoende Planned-tasks kan väljas medan en beroende task väntar.
- [x] **F-27.A2:** READY_FOR_REVIEW eller APPROVED hos beroendet öppnar inte nästa task; verifierad Done gör det.
- [x] **F-27.A3:** Cykel, okänt beroende och saknat mergeunderlag ger konkreta blockerare och ingen start.

**Verifiering:** Kända beroendegrafer med oberoende tasks, cykler och avvikande Kanban/Gitdata.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Worker-underlag F27 (2026-10-09):** TaskSelectionService och läsande MCP task_get_next levererade i task/e07-f27. Fullständiga operatorregistrerade specs/bindings/ordning; grafens hela beroendekedja, native User/Pending, unika taskägare och upptagna Gitresurser kontrolleras. F21/F20/F18/Git/test/stopp/Done-event och tidigare epicers F08/egen acceptans/main/sluttest återläses utan mutation. 51 specifika tester PASS137.50s;44 MCP/startup/CLI-regressioner PASS14.21s. Ruff/build/config/diff/exakta wheelmoduler/rollprompter/länkar PASS. Verkliga temporära Git/SQLite/F21/F08 och SDK-MCP; board/runtime/processobserver simulerade, ingen native parallellitet eller säkrad autonomi påstås. Två felaktigt konstruerade negativa testfixtures rättades (scopebunden Coordinator och tidigare registrerad epicbas), utan svagare produktgrindar. [Kontrakt och verifiering](docs/scheduling/F-27-korbarhet.md). READY_FOR_REVIEW; kriterier/status Done kräver faktisk Integration-review/merge och tester på epic.

**Integration/slutleverans F27 (2026-10-09):** Full8filsreview a815f1b929fc5fbd378c8cf75ec37ce6fe8ed7a2 mot ec28744f358a818913746560f83e9429caa2517c;58834bytes/SHA256d8ab9bc17817287ecce9f9128b637c91a161860fa9067d2474aff2163d1f7ed2 APPROVED. Faktisk bootstrapGitAdapter no-ffTask→Epic161d3f5ba98b1ae12717f549a98f69f6d6e51dfd/operation9bfa6777-b3ec-44bd-8fdb-a7f2ecf27753/exakta parents/identisktreviewtree. På faktiskmerge172relevanta tester145.70s/exit0 och Ruff/build/config/diff/exaktawheelmoduler/rollprompter/länkar PASS. Source/epic ordinariepush och remoteSHA återlästa. NativeF27Donev5 återläst, User105; acceptans3/3. E07Active/egen0av3, kvar F28–F30 och samlad/native epicverifiering/mainintegration. [Sparad review](docs/reviews/F-27.md). Nästa prioriterade task F28; inget ändrat leveransberoende.

### Task F-28 Reservera högst två aktiva Worker slots

**Epic/fas/prioritet:** E-07 / 7 / P0. **Kanban-status:** Done. **TeamPlayer Task-ID:** `3f47197b-324f-4b02-87a7-759fc22620ce`.

**Körbar:** Nej — återöppnad CLI-korrigering är granskad, integrerad och verifierad Done i E07.

**Källa:** A §§17, 29–30, 41; W §§8, 10, 18, 26. **Berör:** Scheduler, slotpersistens, taskclaim.

**Beroenden:** F-27. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Utöka Worker-start från en till två slots med atomisk reservation och unikt ägarskap per run. Implementera claim/release enligt D-03 och verifiera race mellan ny start och återupptagning. Behåll stöd för workergräns ett i testläge.

**Start/precisering F28 (2026-10-09):** User105/Write, F27Donev6/E07merge161d3f5/172tester och E06Done på main verifierade. Integration plockar F28; task/e07-f28 från aktuellE07f9ec93d, utan adopterade produkt-runs. En gemensam intern slotpolicy används av ny taskclaim, runtime-start och samma-session-resume i SQLite-transaktioner. Settings.max_workers=1 eller2 respekteras; befintliga slotreservationer är beständiga i TaskRun och taskägaren unik enligt befintligt schema2. CLAIMED/STARTING/review/fix och okända sidoeffekter behåller kapacitet. Ett misslyckat försök före någon runtime-startavsikt/bindning kan frigöra reservation när tjänsten verifierat att ingen Worker ens kunde startas; samma task/run/operation återanvänds vid säkert retry. Efter runtime-startavsikt krävs F13:s faktiska stopp/inaktivitetsbevis före release. Parked/Done utan sådant bevis eller motstridiga reservationer spärrar ny kapacitet. Ingen agent får välja slot eller skicka ett inaktivitetsboolean. Native parallellitet provas i F30; F28 använder kontrollerad runtime och riktiga samtidiga Git/SQLite-serviceanrop.

**Resultat och kontrakt:** CLAIMED och STARTING reserverar kapacitet. Reservationen hålls genom arbete och review/fix till bekräftad parkering eller avslut; parkerad session räknas inte. Ledig kapacitet beräknas från egna reservationer och bekräftad runtime, inte bara WORKING eller Kanban Active.

**Acceptans**

- [x] **F-28.A1:** Tre samtidiga startförsök ger högst två reserverade aktiva Workers.
- [x] **F-28.A2:** Samma task kan inte äga två slots eller startas av två schedulervarv.
- [x] **F-28.A3:** Startfel och bekräftat sessionsavslut frigör rätt reservation; osäkert stopp frigör den inte.

**Verifiering:** Samtidiga serviceanrop mot temporär SQLite och kontrollerad runtime med långsam start och okänt stopp.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Worker-underlag F28 (2026-10-09):** Gemensam intern WorkerSlots-policy för F15/F11/F13, atomisk reservation i schema 2, max_workers 1–2 och unik run. Säker pre-runtime-release återanvänder samma run/intent/bas; efter startintent krävs F13:s faktiska stoppbevis. Motstridiga claims, felaktiga bindings, otillräckligt stopp och sänkt kapacitetsgräns spärrar ny start. På slutlig källa: `uv run --locked pytest tests/test_worker_slots.py tests/test_task_start.py tests/test_runtime_start.py tests/test_runtime_lifecycle.py tests/test_task_merge_service.py tests/test_task_selection.py tests/test_teamplayer_sync.py tests/test_mcp.py -x` — 227 PASS, 617.63 s, exit 0. De 19 slotproven använder konkurrerande serviceanslutningar, verklig temporär Git/SQLite och kontrollerad runtime/processobserver. Ruff/build/config CLI/diff, 51 exakta wheelmoduler, två rollpolicies och 100 dokumentlänkar PASS. Två felaktiga testantaganden om startordning respektive ändrad retry-timeout rättades utan svagare produktgrindar. Ingen native parallellitet eller full autonom sandbox påstås; F30/F38 återstår. [Kontrakt](docs/scheduling/F-28-slots.md). READY_FOR_REVIEW; Done kräver faktisk Integration-review, merge och eftertest.

**Integration/slutleverans F28 (2026-10-09):** Full 12-filsreview cb4fd0dca7294879b5e0d1db399eb2ada31aeb37 mot f9ec93d4ec9a119dac8d3029aa80d61e8489fddd, 60203 bytes/SHA256 ce45b18b84d348ebb2764fe2225c54c4cd8fdf683f16b85ca303ab34e30cfdfe, APPROVED. Faktisk bootstrap-GitAdapter no-ff Task→Epic ba47a8a4bc465f51a57fb258ff3faef29a6f1340, operation 435f4dae-bea7-4f36-9725-f731a2842717, exakta parents och identiskt reviewtree. På faktisk merge: 98 slot/lifecycle/taskmerge/persistens/state-tester PASS, 183.04 s, exit 0; Ruff/build/config/diff/exakta wheelmoduler/rollpolicies/101 länkar PASS. Source och epic pushade, remote-SHA samt oförändrad main ec28744 återlästa. Native F28 Done v4/User105 verifierat; egen acceptans 3/3. E07 kvar Active, egen acceptans 0/3; F29–F30 och samlat native prov/mainintegration återstår. [Sparad review](docs/reviews/F-28.md). Nästa prioriterade task F29; ingen ändrad leveransordning.

**Coordinator-korrigeringsbeslut F28 (2026-10-09):** Samlad E07-grind på produktkod f267306/metadata17b2cfd gav 356 PASS och ett fel i äldre F15 CLI-test: hårdkodad förväntan max_workers=1 medan faktisk grundkonfiguration är2. Detta strider mot redan beslutat F28-kontrakt1–2, inte mot taskens krav. F28 återöppnas Active nativev6, User105, för enbart testkorrigering med explicit max_workers1 respektive2 och bibehållen kontroll av scope/ingen implicit runtime. Ingen produktkod, schema eller policy ska ändras. Integration synkade aktuell17b2cfd till ursprunglig task/e07-f28 med bootstrapGitAdapter no-ff3b4d6ba6448deb87922e0ddb01223f5b2e8e8c6c,operation586b853b-e056-4291-b3da-8c369498fb02; exakta parents/branches/paths/clean och treeidentitet verifierade. Tidigare F28/F30-reviews, merge-SHA och nativeprov bevaras. Worker implementerar/testar/committar här; ny faktisk review/Task→Epic/eftertest krävs före F28 Done igen. E07 förblir Active; PR7draft och mainintegration väntar. Slutför detta före nästa feature, utan ändrad backloggordning.

**Worker-korrigering F28 (2026-10-09):** CLI-kontraktsprovet använder nu explicit operatörskonfiguration max_workers=1 respektive2 och verifierar båda, samma Integration-principal och inga implicit startade runs. `uv run --locked pytest tests/test_task_start_cli.py tests/test_worker_slots.py -x` — 23 PASS/20.04s/exit0; Ruff/diffcheck PASS. Endast detta test och leveransmetadata ändrades. Worker lämnar READY_FOR_REVIEW på task/e07-f28 från faktiskt synkad aktuell E07; ny Integration-review/merge/eftertest återstår före Done.

**Integration/korrigering F28 (2026-10-09):** Ny full tvåfilsreview source7a08f250c81a291f363ad9cff330c24dcd655174 mot17b2cfda30f7110fbc30ae3b0c46b10338f4f908,9301bytes/SHA256e09e88b448146d53b16c400ce8ace8d7b3618c6f70364ad829a48db4db9665cc APPROVED. Faktisk bootstrapGitAdapter no-ffTask→Epic9ab9252a507b4015091f5b4db955cf4b87470af0,operationa648c0a7-9834-45e0-9d57-a288edcb32d6; exakta parents/identisk reviewtree. På faktisk merge: `uv run --locked pytest tests/test_task_start_cli.py tests/test_mcp.py -x` — 15 PASS/5.37s/exit0; source CLI/slots23PASS20.04s; Ruff/diff PASS. NativeF28Donev9/User105 återläst; tidigare acceptans/nativebevis oförändrade. [Kompletterad review](docs/reviews/F-28.md). E07 Active och samlad regression fortsätter med resterande210tester; tidigare356PASS/122PASS återanvänds bara för exakt oförändrade produkt-/testfiler. Ingen mainmerge förrän komplett grind och aktuell slutreview.

### Task F-29 Driv scheduling och serialisera taskintegration

**Epic/fas/prioritet:** E-07 / 7 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `56b76e54-d870-452a-a477-4d4d4177260f`.

**Körbar:** Nej — verifierad och integrerad i E07; tasken är Done.

**Källa:** A §§17–18, 21, 29, 41; W §§23–28. **Berör:** Schedulerloop, mergekö, status/events.

**Beroenden:** F-28. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera deterministisk service-loop för start, observation, reviewkö och påfyllnad efter avslut. Serialisera merge per epic och verifiera tasken mot den epicversion som gäller vid integration. Pausa berört arbete vid fel utan att starta samma task på nytt.

**Start/precisering F29 (2026-10-09):** User105/Write och samtliga native beroenden Done verifierade; F28 Done v5 efter granskad E07-merge ba47a8a/98 eftertester, E06 Done på main. Integration plockar F29; separat task/e07-f29 från aktuell E07 c4d789e genom bootstrap-GitAdapter utan adopterade produkt-runs. En begränsad async service-tick observerar egna registrerade runs, driver start/rapport/review/leverans/statussynk och fyller säkra lediga slots från nytt F27-urval. Ingen långlivad agent eller implicit autonom drift införs före E09/F38. Betrodd operatörskomposition registrerar fullständiga specs, native UUID-bindings, tidigare epicbevis, runtime och reviewkonfiguration. Claim återvaliderar full körbarhet inom samma Gitlås/SQLite-transaktion som F15:s beständiga ägarskap/slot; claimbevis sparas före Git/runtime. Ny boardläsning och F25:s autentiserade bindning/CAS-synk kontrollerar tilldelning/kontrakt före fortsatt start. Ingen atomisk transaktion över TeamPlayer/Git/SQLite påstås. Projektets schedulerlås serialiserar ticks och F21:s befintliga lås/deliveryintent serialiserar faktisk integration även mot andra serviceanrop. Stabil journal per task/handoff/context återanvänder kända sidoeffekter efter upprepade events/omstart. Review sker seriellt mot aktuell epic, aldrig automatiskt godkänt av scheduler: befintliga reviewverktyg eller en betrodd operatörsfunktion lämnar versionsbundet beslut. Enbart ändrad epicbas kan ogiltigförklara befintlig approval och återköa samma oförändrade verifierade task genom en intern, faktakontrollerad övergång APPROVED→READY_FOR_REVIEW; historiken och slot/session bevaras, ny full review/test krävs. Ändrad taskkod, konflikt, okänt test/start/stopp och andra fel pausar berört pipeline-steg med beständig orsak; ingen ersättningsrun, blind omstart eller nytt testförsök gissas. TeamPlayer-synkfel återförsöker enbart synk när lokal leverans redan är verifierad. Manuell utvecklingsbootstrap adopteras inte; kontrollerade Worker-scenarier med riktig Git/SQLite/MCP provas här, native tre-taskprov i F30.

**Kontrakt för parallella rapporter F29 (2026-10-09):** Det verkliga två-Worker-serviceprovet visade F16:s tidigare en-Worker-begränsning: B:s native slutcommit saknar A:s senare epicmerge och kan därför inte ge ett aktuellt reviewunderlag före F18:s synk. Worker-rapport/test använder nu en separat läsande källsnapshot mot F05:s verifierade taskbas eller senaste faktiska F07-synks source/epiccommit. Exakta branch/path/source/base, ren Git, original native rapportproveniens och testsresultat krävs fortfarande. Verifieringsjournalen märks purpose=worker_report/source_base_commit och saknar aktuellt review-target; den kan inte användas för approval eller leverans. F18 synkar därefter till aktuell epic och kör nytt test på exakt aktuellt task/epic-par före F20/F21. Ingen native rapportcommit skrivs om eller fabriceras. F25:s dependencybindning läser unika registrerade runs inom samma projekt även för tidigare epics; F27:s faktiska main-/acceptansbevis krävs före sådan claim. Scheduler äger en beständig Integration-principal per produkt-epic, förhöjer ingen roll och kräver tidigare Coordinator-bindning/start. MCP task_schedule aktiveras endast genom explicit betrodd Python-komposition och tar bara project/epicrun, inte specs, beslut, roller, slots eller återförsöksbooleans. Om en synk återstår efter verklig leverans återförsöks enbart F25-synken; Git/runtime/test upprepas inte.

**Worker-underlag F29 (2026-10-09):** Beständig begränsad TaskSchedulerService och operatörsaktiverat MCP task_schedule levererade. Full F27-graf återvalideras i F15-claimtransaktionen före Git/runtime; F25-bindning/CAS och färska boardläsningar före start. Projektlås och F21 serialiserar aktuell review/merge; ingen rollförhöjning, automatisk approval eller bootstrapadoption. Worker-källtest skiljs från aktuellt reviewpar för parallella rapporter. Stabila task/handoff/context/deliveryjournaler, pause med konkret orsak och enbart synk-retry efter lokal Done. Slutlig källa: `uv run --locked pytest tests/test_task_scheduler.py tests/test_states.py tests/test_task_start.py tests/test_git_integration.py tests/test_worker_report_service.py tests/test_task_review_service.py tests/test_task_changes_service.py tests/test_task_approval_service.py tests/test_task_merge_service.py tests/test_task_selection.py tests/test_teamplayer_sync.py tests/test_mcp.py -x` — 323 PASS, 918.98 s, exit 0. Nio schedulerprov visar A/B, C efter A, aktuell B-review, repeat/reopen/lås/identitet, riktig SDK-MCP, ägarrace, äldre approval, ändrad taskkod, testfel, osäkert stopp och förlorat synksvar; verklig Git/SQLite/testprocesser med kontrollerade board/runtime/process-/beslutsadaptrar. Ruff/build/config/diff, 52 exakta wheelmoduler, två rollpolicies och 119 lokala länkar PASS. F27:s befintliga låsta grafkropp har identisk AST efter refaktorering. Native parallellitet och autonom miljögate återstår i F30/F38. [Drift och kontrakt](docs/scheduling/F-29-scheduler.md). READY_FOR_REVIEW; Done kräver faktisk Integration-review/merge och eftertest.

**Resultat och kontrakt:** Loopen är servicebaserad i fas 7; den långlivade Integration Agent tar besluten via verktygen i E-09. Två arbetande tasks kan producera resultat samtidigt men endast en leveransmerge åt gången. Ändrad epic ogiltigförklarar ett äldre godkännande.

**Acceptans**

- [x] **F-29.A1:** När task A avslutas startar nästa körbara task i frigjord slot utan manuell tilldelning.
- [x] **F-29.A2:** Två samtidiga taskresultat integreras seriellt och task B verifieras mot epic efter A.
- [x] **F-29.A3:** Ett upprepat eventsvar eller schedulervarv ger ingen dubbel Worker, review eller merge.

**Verifiering:** Serviceintegration med två kontrollerade Workers, samtidigt färdigställande och förändrad epiccommit.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Integration/slutleverans F29 (2026-10-09):** Full 19-filsreview 6b59c3a01017a84bf15d1dd01189b89401836675 mot c4d789ee5a0319eb3a3fc79dccd15ad7641e45e0, 129575 bytes/SHA256 4108449f73fae3a3c0bea0b1c4610f7c926aaab2249aabe6415585c1d1340bc0, APPROVED. Faktisk bootstrap-GitAdapter no-ff Task→Epic 66f381e4c4654701e5c755e580c9fe6ddfb367ed, operation 3a8a348f-23cc-4639-a91a-c317767c8f96, exakta parents och identisk granskad tree. På faktisk merge: 103 scheduler/state/approval/taskmerge/slot-tester PASS, 339.71 s, exit 0; Ruff/build/config/diff/52 exakta wheelmoduler/två rollpolicies/120 länkar PASS. Source och epic pushade och remote-SHA återlästa; main ec28744 oförändrad. Native F29 Done v5/User105 återläst; taskacceptans 3/3. E07 kvar Active, egen acceptans 0/3 inför F30:s native prov och samlad slutreview/mainintegration. [Sparad review](docs/reviews/F-29.md). Nästa prioriterade task F30; ingen ändrad ordning.

### Task F-30 Verifiera tre tasks med två parallella Workers

**Epic/fas/prioritet:** E-07 / 7 / P1. **Kanban-status:** Done. **TeamPlayer Task-ID:** `5618ec4b-f817-4789-b45c-284e29199936`.

**Körbar:** Nej — granskad, integrerad och verifierad Done i E07.

**Källa:** A §§41, 48; W §§8–10, 23–28. **Berör:** Integrationsscenario, driftinstruktion.

**Beroenden:** F-29. **Externa förutsättningar:** X-01 och X-02; testepic med tre små och oberoende verifierbara tasks.

**Arbetsinstruktion för Codex:** Bygg en liten testepic med A och B oberoende samt C beroende av integrerad A. Kör serviceflödet med två Workers, review och taskmerge. Samla tidslinje, slotreservationer, commits och Kanbanförändringar.

**Start/avgränsning F30 (2026-10-09):** Integration verifierade User105/Write, F29 Done v6 efter granskad E07-merge 66f381e/103 eftertester och samtliga native beroenden Done; E06 finns verifierad på main. task/e07-f30 från aktuell E07 6c711d5 genom bootstrap-GitAdapter, inga produkt-runs adopterade. Separata F30-native test-ID:n, egen databas/provjournal och uttryckligt namngiven Herdr-server. Återanvänd den tidigare faktiskt godkända ofarliga F26-reporoten; bevara dess main, gamla branches/worktrees/provjournaler och skapa nya faktiskt F05-ägda F30-resurser utan adoption. En registrerad test-Coordinator förbereder nya epicens AGENTS/README-regler före taskstart så gamla F26-prefixinstruktioner inte gäller F30. A/B är oberoende små funktioner, C använder granskad/integrerad A. F29:s tick väljer/startar/levererar/synkar tasks; operatören ger aktuella faktiska reviews, ingen manuell scheduling eller implicit approval. Samla SQLite-events, operationer, exakta source/target/review/merge/test/stoppbevis, native session/process/worktree samt överlappande working-observationer och skyddad full boardbaslinje. Kontrollera högst två reservationer vid varje domänövergång och observerad native snapshot, samt A-Done/merge/test före C-claim. Fixture-epicen förblir Active efter task-Done eftersom detta prov inte omfattar dess mainintegration/E10. F38:s autonomi-/miljögate kvarstår. Native fixture-skrivningar sker i separat provoperatörsroll efter granskad harness, aldrig som utvecklingens Worker-roll.

**Avstämning/precisering F30 (2026-10-09):** Första lokala F05-förberedelsen skapade faktiskt ägd PLANNED epic och seed cb1d80f2739d8b8c89391dbdeed91d76d056db48 ovanpå oförändrad tidigare fixturemain d4215a26. Harness använde fel metadata-API för current_commit; strikt StateStore avvisade uppdateringen och ingen native task/Worker eller TeamPlayer-fixture hade ännu skapats. Bevara samma Git/DB och avsluta endast känd saknad checkpoint efter ägar-/branch/path/bas/parent/full-diff-kontroll; kör inte prepare eller commit igen. Samma API-användning i F29:s interna approval-requeue rättas inom F30:s verifieringsscope: verifierade commitfält går via update_run_metadata, runtimefält via update_runtime_metadata. Ett konkret serviceprov med ändrad faktisk epic-HEAD före metadataavstämning ska kräva ny review och behålla samma task/session/slot; detta ändrar inga acceptanskriterier och ger ingen ny runtime- eller mergebehörighet.

**Native första försök/nytt avgränsat prov (2026-10-09):** Första försöket `.herdr/probes/f30` är underkänt och bevaras. A:s native Codex-start tog emot assignmenttransport medan en uppdateringsdialog var aktiv, körde updater och avslutades före verifierad ACK/session; F29 pausade säkert utan omstart. B implementerade men ACK-deadline löpte ut under operatörens felsökning; ingen taskmerge eller Done registrerades. B stoppades och parkerades faktiskt genom F13; A:s gamla reservation behålls konservativt, utan fabricerad stopprecovery. Efter kontroll av egna agents/processer stoppas endast den egna första testservern. Ett nytt sekventiellt F05-prov med egen `.herdr/probes/f30-r2`, nya lokala project/epic/run/branch/worktree-ID:n och ny namngiven server använder samma godkända reporot och samma tre externa fixture-ID:n som fortfarande är Pending. Ingen extern rollback, ersättning av task-ID eller adoption av gamla runs. Native startup-preflight granskar synlig UI och avfärdar bara kända frivilliga banners; erbjudande om update/trust/approval får inte besvaras av assignmenttransport. Scheduler drive fortsätter observera ACK inom ursprunglig deadline även om en annan task pausas. F47-recovery införs inte här. Det nya provet måste fortfarande själv styrka samtliga F30-kriterier; det gamla får aldrig räknas som lyckad parallellitet.

**Resultat och kontrakt:** Minst två Worker-intervall ska överlappa så att provet visar verklig parallellitet. C får starta först efter A:s granskade integration och verifiering. Epic-slutreview och main-merge tillkommer i E-10.

**Acceptans**

- [x] **F-30.A1:** A och B arbetar samtidigt i olika worktrees och C tar ledig slot efter godkänd A-integration.
- [x] **F-30.A2:** Alla tre tasks blir Done med rätt commits och testunderlag utan manuell scheduling.
- [x] **F-30.A3:** Ingen tidpunkt visar fler än två aktiva/reserverade Workers eller merge mot stale approval.

**Verifiering:** Verkligt Herdr/Codex- och TeamPlayer-prov samt maskinellt kontrollerad tidslinje från events.

**Verifierat nativeprov F30 (2026-10-09):** [drift/prov](docs/scheduling/F-30-nativeprov.md)/[publicproof](docs/scheduling/F-30-prover.json). Verklig Herdr0.9.3/protokoll22, Codex0.162.0, Git/SQLite/TeamPlayer: 15 bracketade A/B-working-overlap, olika worktrees/sessioner/processer, 28 domänövergångar med högst två reservationer. F29 väljer/startar alla tre; C:s slot1/claim efter faktisk granskad A-Done/merge637bd2e/test/stop. Fyra explicit faktiskt granskade approvals; gammal B-approval mot seed a3d0aaff återköad efter A-merge, ny full review och samma B-session/slot innan merge78e2815. C efter aktuell review mergad6308fb6. Oberoende tester på source/aktuell target/merge och F13-fysisk stop före varje Done; native tasks Done v10/v11/v8, User105; separat fixtureepic840d61d5 Activev5, dess mainintegration ingår inte. Fixturmain d4215a26 och gamla refs/worktrees oförändrade; full F24-baslinje för övriga boardobjekt exakt oförändrad vid export. Extra scheduler-tick skapade inga runtime/assignment/merge/leveranstest/stopp och behöll runs/sessioner/commits; egna testservrar stoppade efter faktisk inaktivitet, journaler bevarade. Första underkända försöket bevaras och ingår inte i lyckad acceptans. Metadataregression34PASS/86.84s, F29-staleapprovaltest1PASS/24.53s, timeline/startup18PASS/0.85s. F30.A1–A3 faktiskt verifierade; utvecklings-F30 fortsatt Active inför egen full review/Task→Epic/integrationskontroller. F38 och F47 framställs inte som levererade.

**Slutlig källverifiering F30 (2026-10-09):** `uv run --locked pytest tests/test_task_approval_service.py tests/test_task_scheduler.py tests/test_f30_timeline.py -x` — 49 PASS/197.13s/exit0. Ruff, build, config-CLI och diffcheck PASS; 52 Pythonmoduler och två rollpolicies i wheel exakt mot källan, 106 berörda lokala länkar PASS. Publicproof kontrollerad mot faktiska Git-parents, approval-source/target, post-mergetest och stopp. Worker lämnar READY_FOR_REVIEW från task/e07-f30; task-Done kräver fortfarande Integration-review/Task→Epic/eftertester.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

**Integration/slutleverans F30 (2026-10-09):** Full åttafilsreview source1ad07aa39965ccc61c672348eb956f2c51d699da mot aktuellE07 6c711d5a43cc7c80506cb81593b601ec0ec6b31c,135906bytes/SHA2568e554cea962be09f78024931a94808ab4f37ef50755ae0e0629f9198dc592413 APPROVED. Faktisk bootstrap-GitAdapter no-ff Task→Epic f26730670cd36647e8ed62bcd7754011b474b77c,operation9da3c5db-e0c2-4d21-9790-c74e7e32a7a8; exakta parents och identisk granskad tree. På faktisk merge: `uv run --locked pytest tests/test_task_scheduler.py tests/test_states.py tests/test_task_approval_service.py tests/test_task_merge_service.py tests/test_worker_slots.py tests/test_f30_timeline.py -x` — 122 PASS/348.57s/exit0. Ruff/build/config/diff/exakta52wheelmoduler/tvårollpolicies/106länkar PASS; publicfiler kontrollerade mot aktuella privata header-/bearervärden utan läckage. F30.A1–A3 uppfyllda; native utvecklingstask Donev8/User105 återläst. E07 egen acceptans3/3 från nativeprov och samtidighets-/aktuell-reviewgrindar, fortsattActive inför samlad slutreview/PR/mainintegration. Manuella bootstrapworktrees integreras efter faktisk review/path/branch/bas/SHA-kontroll utan adopterade produkt-runs. [Sparad review](docs/reviews/F-30.md). Nästa steg är samlad E07-leverans; F31 startas först efter verifierad E07-Done på main.

**Coordinator/slutleverans E07 (2026-10-09):** Slutreview APPROVED för epic `9a4e8b15e2261339fa47c2512b4b78e1f15419ed` mot main `ec28744f358a818913746560f83e9429caa2517c`: komplett 39-filsdiff, 382454 bytes, SHA256 `eabf913d82f6ba30e893a9851cb6d08590547de7759f96187a9bfe46cd417dea`. Samlad verifiering: 692/692 unika aktuella testfall PASS i disjunkta delmängder 122+356+4+210; tidigare passerade produkt-/testfiler har identiska Gitblobbar. Ett äldre CLI-test stoppade först grinden och rättades genom dokumenterad F28-återöppning, ny full review och faktisk integration; det underkända kommandot framställs inte som PASS. PR https://github.com/johanLstang/HerdrCoordinator/pull/7 är återläst MERGED med exakt mainmerge `9ff5eb8698f2a0750d5eea3309a6ccb2d3c7aadd`, operation `379552d3-fcd2-489e-b94d-cf2f3f847993`, exakta parents och identisk godkänd tree. På faktisk mainmerge: 119 tester PASS/141.54s/exit0 samt Ruff/build/configCLI/diff, 52 exakta wheelmoduler, två rollpolicies och 120 berörda länkar PASS. Remote-main återläst. F27–F30 native Done/User105; E07 egen acceptans 3/3, native Donev14 återläst efter alla grindar. Bootstrap GitAdapter-undantaget användes efter faktiska path/branch/bas/clean/SHA/reviewkontroller, utan fabricerade produktruns eller ägarskap. Native prov visar två Workers, C efter A:s verifierade leverans, aktuell B-review och replay utan dubblering. Schema2/uv.lock oförändrade; långlivade loopar, F38-autonomigate och F47-recovery återstår. Gamla och nya provresurser bevaras. [Sparad slutreview](docs/reviews/E-07.md). Nästa genomförbara feature är F31 i E08 från verifierad aktuell main.

## Epic E-08 Parkera blockerade tasks och återuppta samma arbete

**Fas:** 8. **Prioritet:** P1. **Kanban-status:** Planned. **TeamPlayer Epic-ID:** `dc637987-8dce-49ef-a178-7ed819ce6db7`.

**Körbar:** Ja — E07 Done/PR #7/main9ff5eb8 och 119 sluttester verifierade; externa villkor kontrolleras per task. **Beroende:** E-07 Done på main.

**Källa:** A §§23, 42; W §§16–18, 22, 38–39.

### Resultat och omfattning

En blockerad task syns i Attention, frigör kapacitet och fortsätter i samma session när nödvändig input och en slot finns.

**Ingår:** Blockerarrapport, verklig parkering, sparad input, väntan på slot och återupptagning. **Utanför:** Automatiskt gissade svar, ersättning av saknat worktree och osynligt skapande av ny task/session.

**Gemensamma regler:** tillämpa samtliga sex kontrakt ovan och de ingående taskernas förvillkor. **Risk och beslutspunkt:** Parkering måste stoppa aktivitet innan sloten frigörs. Saknat bekräftat parkstöd från F-10/F-13 blockerar liveacceptans.

### Tasks och beroenden

| Task | Leverans | Taskberoenden utöver epicens beroende | Prioritet |
| --- | --- | --- | --- |
| F-31 | Registrera blockerare och parkera Worker säkert | E-07 | P0 |
| F-32 | Återuppta Attention med sparat beslut och ledig slot | F-31 | P0 |
| F-33 | Verifiera att Attention inte stoppar andra tasks | F-32 | P1 |

### Epicacceptans

- [ ] **E-08.A1:** Blockerarrapport med konkret orsak och inputbehov ger Attention och bekräftat parkerad session.
- [ ] **E-08.A2:** En annan körbar task använder frigjord slot medan den första väntar.
- [ ] **E-08.A3:** Samma Worker återupptas med sparat beslut utan att workergränsen överskrids.

Epicen följer dessutom den gemensamma definitionen av Done. Acceptansen verifieras genom taskernas underlag och ett samlat prov av epicens resultat.

### Task F-31 Registrera blockerare och parkera Worker säkert

**Epic/fas/prioritet:** E-08 / 8 / P0. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `26abd83e-7e41-4a41-9524-c827011cbe1e`.

**Körbar:** Ja — E07 Done på main; X01/X02 och faktisk parkförmåga från F13/F26 finns verifierade och kontrolleras inför nytt avgränsat prov.

**Källa:** A §§23, 42; W §§16, 18, 22, 39. **Berör:** task_report_blocked, Herdr, persistens, TeamPlayer.

**Beroenden:** E-07. **Externa förutsättningar:** X-01, X-02 och verifierad parkförmåga från F-10/F-13.

**Arbetsinstruktion för Codex:** Implementera komplett blockerarflöde för Worker och review som kräver extern input. Spara orsak, efterfrågad input och ansvarig roll, skriv Attention och parkera registrerad session. Frigör slot först efter verifierad inaktivitet.

**Resultat och kontrakt:** WORKING/REVIEWING → BLOCKED → PARKED. Parkering behåller session-ID, branch och worktree. TeamPlayer-nätfel hanteras genom synkavsikt. Parkfel eller okänt runtimeutfall behåller reservation och konkret fel, även om Kanban visar Attention.

**Acceptans**

- [ ] **F-31.A1:** Komplett blockerarrapport sparas och publiceras på rätt task med inputbehov.
- [ ] **F-31.A2:** Bekräftad parkering bevarar resurser och frigör precis taskens slot.
- [ ] **F-31.A3:** Dubbel blockerarrapport, främmande task eller misslyckad parkering ger ingen osäker slotrelease.

**Verifiering:** Serviceprov med parkbekräftelse, timeout och TeamPlayer-fel samt verklig parkering.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-32 Återuppta Attention med sparat beslut och ledig slot

**Epic/fas/prioritet:** E-08 / 8 / P0. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `41cb49c7-c172-4182-8d17-8ba2e255f07a`.

**Körbar:** Nej — invänta F-31 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§23, 42; W §§17–18, 22. **Berör:** worker_resume, slotkö, inputhistorik, status.

**Beroenden:** F-31. **Externa förutsättningar:** X-01 och X-02; samma-session-resume verifierad i E-03.

**Arbetsinstruktion för Codex:** Implementera resume_task/worker_resume för behörig Integration-roll. Spara beslutet, kontrollera session/worktree/branch och boka slot atomiskt innan Worker återupptas. Skicka beslutet till samma session och sätt WORKING/Active efter bekräftelse.

**Resultat och kontrakt:** Input utan slot ligger kvar som väntande återupptagning i Attention. Varje svar har identitet och historik; dubbelt anrop skickar inte beslut eller start två gånger. Saknat worktree eller ej återupptagbar session kräver konkret åtgärd, inte en tyst ny Worker.

**Acceptans**

- [ ] **F-32.A1:** Med svar och ledig slot fortsätter samma session i samma branch/worktree och återgår till Active.
- [ ] **F-32.A2:** Med fulla slots väntar återupptagningen utan att en tredje Worker startas.
- [ ] **F-32.A3:** Dubbel input/resume och saknad session/worktree hanteras utan extra run eller förlorat beslut.

**Verifiering:** Samtidiga resume/start-prov, återöppnad databas med väntande svar och verkligt resumeprov.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-33 Verifiera att Attention inte stoppar andra tasks

**Epic/fas/prioritet:** E-08 / 8 / P1. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `f7a0c038-4f49-48d0-ba09-fbca7bfe0e92`.

**Körbar:** Nej — invänta F-32 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§42, 48; W §§16–18, 22, 27. **Berör:** Integrationsscenario, operatörsinstruktion.

**Beroenden:** F-32. **Externa förutsättningar:** X-01 och X-02; testepic med en tydlig blockerarfråga.

**Arbetsinstruktion för Codex:** Kör ett scenario där A blockeras, B fortsätter och C tar A:s slot. Lämna svar till A medan båda slots är upptagna och verifiera senare återupptagning. Dokumentera hur operatören ser blockeraren och lämnar beslut.

**Resultat och kontrakt:** Tidslinjen ska visa parkbekräftelse före slotrelease och bokning före resume. Kanban och runtime kan tillfälligt skilja sig under extern synk men skillnaden måste visas och avstämmas.

**Acceptans**

- [ ] **F-33.A1:** B och C kan leverera medan A är parkerad och Attention.
- [ ] **F-33.A2:** A:s sparade svar leder till samma-session-resume först när en slot är ledig.
- [ ] **F-33.A3:** Samtliga tasks kan senare bli Done utan förlorade worktrees, dubbla Workers eller fler än två aktiva.

**Verifiering:** Verkligt Herdr/Codex/TeamPlayer-scenario med tidslinje, ID-jämförelse och verifierade merge-SHA.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

## Epic E-09 Låt en långlivad Integration Agent driva en epic

**Fas:** 9. **Prioritet:** P1. **Kanban-status:** Planned. **TeamPlayer Epic-ID:** `fd79802a-93dd-4b77-b4b4-71ba93060ad6`.

**Körbar:** Nej — E-08 Done på main; externa villkor anges per task. **Beroende:** E-08 Done på main.

**Källa:** A §§4.2, 19–24, 29, 31, 43; W §§7, 9, 20–30, 38–40.

### Resultat och omfattning

En Integration Agent kan själv välja körbara tasks, styra Workers, granska leveranser och lämna en samlat verifierad epic till Coordinator.

**Ingår:** Epicruntime, långlivad integrationssession, verktyg för scheduling/review och EPIC_READY_FOR_REVIEW. **Utanför:** Slutgodkännande, main-merge och automatiskt val av nästa epic.

**Gemensamma regler:** tillämpa samtliga sex kontrakt ovan och de ingående taskernas förvillkor. **Risk och beslutspunkt:** En långlivad agent kan återupprepa beslut eller missa externa ändringar. Verktygen måste behålla deterministiska villkor och aktuell kontext.

### Tasks och beroenden

| Task | Leverans | Taskberoenden utöver epicens beroende | Prioritet |
| --- | --- | --- | --- |
| F-34 | Starta en enda långlivad Integration Agent per epic | E-08 | P0 |
| F-35 | Låt Integration Agent styra tasks genom verktyg | F-34 | P1 |
| F-36 | Verifiera epicen och lämna komplett reviewunderlag | F-35 | P0 |
| F-37 | Verifiera en epic styrd av Integration Agent | F-36 | P1 |

### Epicacceptans

- [ ] **E-09.A1:** En enda Integration Agent driver hela testepicens taskflöde genom orchestratorverktyg.
- [ ] **E-09.A2:** Alla tasks är verifierat integrerade innan epicens samlade build/test/acceptans körs.
- [ ] **E-09.A3:** EPIC_READY_FOR_REVIEW innehåller aktuell epiccommit och komplett underlag utan main-merge.

Epicen följer dessutom den gemensamma definitionen av Done. Acceptansen verifieras genom taskernas underlag och ett samlat prov av epicens resultat.

### Task F-34 Starta en enda långlivad Integration Agent per epic

**Epic/fas/prioritet:** E-09 / 9 / P0. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `678fb877-d9c7-4d07-a182-9ee79cc0cbdc`.

**Körbar:** Nej — invänta E-08 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§4.2, 19, 29, 31, 43; W §§6–7, 38, 40. **Berör:** epic_start, EpicRun, Integration-prompt, Herdr.

**Beroenden:** E-08. **Externa förutsättningar:** X-01 och X-02; registrerade rollanslutningar enligt F-04.

**Arbetsinstruktion för Codex:** Implementera epic_start som skapar eller återfinner epicruntime och en långlivad integrationssession. Leverera epicmål, tasklista, acceptans, beroenden, källor och rollbegränsningar. Bind anslutningen till rätt epic genom MCP-policy.

**Resultat och kontrakt:** En epic får en ägande Integration-session. Dubbel start återanvänder matchande runtime. Integration Agent får begära taskoperationer för egen epic men aldrig main-merge eller skriva i Workers worktrees. Epic sätts Active när starten är registrerad enligt workflow.

**Acceptans**

- [ ] **F-34.A1:** Giltig epic får en registrerad Integration-session och komplett uppdrag med aktuell branch.
- [ ] **F-34.A2:** Två startanrop ger en enda ägande session och EpicRun.
- [ ] **F-34.A3:** Fel projekt/epic och Integration-anrop för annan epic avvisas utan sidoeffekter.

**Verifiering:** Serviceprov med samtidiga epicstarter och verkligt sessionsprov.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-35 Låt Integration Agent styra tasks genom verktyg

**Epic/fas/prioritet:** E-09 / 9 / P1. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `7f990cb9-be35-485f-a9bc-a3ff468de448`.

**Körbar:** Nej — invänta F-34 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§4.2, 5, 17–23, 43; W §§7–10, 20–28, 39. **Berör:** MCP, scheduler, Review, Integration-policy.

**Beroenden:** F-34. **Externa förutsättningar:** X-01 och X-02 för det verkliga flödet.

**Arbetsinstruktion för Codex:** Koppla Integration Agent till task_get_next, task_start, review, ändringsbegäran, taskmerge och resume. Ge aktuell task/slot/runtimeöversikt efter varje operation. Låt agenten välja bland verifierat körbara kandidater medan servicekontrollerna verkställer besluten.

**Resultat och kontrakt:** Agenten bygger inte egen shelllogik för kritiska operationer och implementerar normalt inte tasks. Alla svar visar aktuell state, nästa tillåtna operation och konkret blockerare. Repetition av beslut är säker; systemet kräver inte att agenten själv minns senaste commit.

**Acceptans**

- [ ] **F-35.A1:** Agenten kan starta två oberoende tasks, hantera review/fix och fylla ledig slot via verktygen.
- [ ] **F-35.A2:** Förslag att starta beroende task för tidigt eller använda stale approval avvisas deterministiskt.
- [ ] **F-35.A3:** Attention/resume och taskstatus hanteras genom rätt services och utan direkt TeamPlayer-skrivning från Worker.

**Verifiering:** Scenario med inspelade agentsvar plus verkligt prov av verktygsstyrt taskflöde.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-36 Verifiera epicen och lämna komplett reviewunderlag

**Epic/fas/prioritet:** E-09 / 9 / P0. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `e8e489aa-0235-436a-adc0-4c61703dab9e`.

**Körbar:** Nej — invänta F-35 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§24–25, 43; W §§29–31. **Berör:** epic_complete, epicacceptans, testkörning, rapport.

**Beroenden:** F-35. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera samlad epicverifiering när alla ingående tasks är Done. Kör betrodd build/test/acceptanskonfiguration och sammanställ taskresultat, reviews, merge-SHA, diff mot main och kända begränsningar. Persistéra EPIC_READY_FOR_REVIEW med aktuella commits.

**Resultat och kontrakt:** Taskmedlemskap och acceptansversion ingår i underlaget; ny eller återöppnad task gör det inaktuellt. En tom tasklista eller saknade kriterier räknas inte som verifierad epic. Testfel lämnar epicen Active med konkret åtgärd, inte Done.

**Acceptans**

- [ ] **F-36.A1:** Alla verifierade tasks och godkänd epicacceptans ger komplett EPIC_READY_FOR_REVIEW.
- [ ] **F-36.A2:** Ej-Done-task, saknat underlag eller misslyckad build/test stoppar överlämningen.
- [ ] **F-36.A3:** Ändrad epiccommit eller tasklista kräver nytt underlag; operationen gör ingen main-merge.

**Verifiering:** Serviceprov med varierat taskmedlemskap, testfel och commits samt diffkontroll i temporärt Git.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-37 Verifiera en epic styrd av Integration Agent

**Epic/fas/prioritet:** E-09 / 9 / P1. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `b8e3bd6d-2287-447a-9f48-bb7863101cd6`.

**Körbar:** Nej — invänta F-36 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§43, 48; W §§7–10, 20–30. **Berör:** Verkligt integrationsscenario, driftinstruktion.

**Beroenden:** F-36. **Externa förutsättningar:** X-01, X-02 och möjlighet att ge specificerad blockerarinput.

**Arbetsinstruktion för Codex:** Kör en testepic med tre tasks, ett beroende, en korrigering och en Attention-period. Låt Integration Agent styra alla taskbeslut genom MCP och lämna en verifierad epicrapport. Samla run-, sessions-, review- och mergehistorik.

**Resultat och kontrakt:** Underlaget ska visa en långlivad integrationssession, egna Worker-sessioner per task och samma session vid fix/resume. En människa lämnar extern input när Attention behöver den. Main-merge lämnas till Coordinator-epicen.

**Acceptans**

- [ ] **F-37.A1:** Agenten driver testepicen till EPIC_READY_FOR_REVIEW utan manuell scheduling eller taskmerge.
- [ ] **F-37.A2:** Beroende, korrigering och Attention hanteras med rätt sessionsidentitet och workergräns.
- [ ] **F-37.A3:** Rapporten kan verifieras mot Git, tester och TeamPlayer; main är oförändrad.

**Verifiering:** Verkligt testrepository och testepic med före/efter-SHA och fullständig rapport.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

## Epic E-10 Slutgranska integrera och välj nästa epic med Coordinator

**Fas:** 10. **Prioritet:** P1. **Kanban-status:** Planned. **TeamPlayer Epic-ID:** `559a4c95-3282-4b4b-a30f-97f9d0dbbaea`.

**Körbar:** Nej — E-09 Done på main; externa villkor anges per task. **Beroende:** E-09 Done på main.

**Källa:** A §§4.1, 25–27, 31, 44, 47–48, 50; W §§5–6, 30–34, 37–40.

### Resultat och omfattning

En långlivad Coordinator kan välja nästa körbara epic, ta emot verifierad leverans, begära korrigering, integrera till main och fortsätta med nästa epic.

**Ingår:** Epicval/claim, Coordinator-policy, slutreview, korrigeringsloop, main-merge och första kompletta autonoma flödet. **Utanför:** Samtidiga epics och automatisk lösning av main/epickonflikter.

**Gemensamma regler:** tillämpa samtliga sex kontrakt ovan och de ingående taskernas förvillkor. **Risk och beslutspunkt:** Slutreview måste gälla aktuell main och alla taskleveranser. Runtimebehörigheterna från D-02/F-17 måste vara verifierade innan autonom merge aktiveras.

### Tasks och beroenden

| Task | Leverans | Taskberoenden utöver epicens beroende | Prioritet |
| --- | --- | --- | --- |
| F-38 | Välj och claima nästa epic med Coordinator | E-09 | P0 |
| F-39 | Slutgranska epic och återför korrigeringskrav | F-38 | P1 |
| F-40 | Integrera epic och fortsätt efter slutverifiering | F-39 | P0 |
| F-41 | Verifiera första kompletta autonoma epicflödet | F-40 | P1 |

### Epicacceptans

- [ ] **E-10.A1:** Coordinator väljer endast prioriterad körbar epic och startar högst en aktiv epic i MVP.
- [ ] **E-10.A2:** Slutreview kan begära korrigering och därefter godkänna aktuell epic för main-merge.
- [ ] **E-10.A3:** Epicen blir Done efter main-merge och slutverifiering; nästa beroende epic startar från nya main.

Epicen följer dessutom den gemensamma definitionen av Done. Acceptansen verifieras genom taskernas underlag och ett samlat prov av epicens resultat.

### Task F-38 Välj och claima nästa epic med Coordinator

**Epic/fas/prioritet:** E-10 / 10 / P0. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `64b97b6e-2251-4964-9b67-f67d709de9bf`.

**Körbar:** Nej — invänta E-09 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§4.1, 5–6, 29, 31, 44; W §§5–6, 34, 38. **Berör:** Coordinator-session, epic_claim_next, projektpolicy.

**Beroenden:** E-09. **Externa förutsättningar:** X-01, X-02 och verifierad runtimepolicy enligt D-02.

**Arbetsinstruktion för Codex:** Implementera långlivad Coordinator-prompt/session och epic_claim_next från TeamPlayer med prioritet, beroenden och stabil ordning. Reservera projektets enda aktiva epic atomiskt. Kontrollera D-02/F-17 och blockera autonom körning vid olösta nödvändiga runtimegränser.

**Registrerat runtimehinder F-17 (2026-10-08):** Autonom drift/merge blockeras tills outside-read till skyddade resurser, Git-/approvaleskalering, osandboxade måltester och startup input-readiness har verifierade gränser. Se [prov och konkret nästa åtgärd](docs/worker/F-17-verklig-worker.md). Coordinator/operatör väljer och säkrar målmiljön; Integration gör faktiska negativa prov. F-38 förblir Planned fram till sin fas; detta är dess dokumenterade externa gate.

**Resultat och kontrakt:** Epicberoenden kräver verifierad main-merge. Taskerna under vald epic måste ha tillräckligt underlag för planering. Dubbelt claim återfinner befintlig run. Om inga körbara epics finns lämnas vänteläge med orsaker, inte ett artificiellt Done eller oändlig startloop.

**Acceptans**

- [ ] **F-38.A1:** Högst prioriterad körbar epic väljs och startar sin Integration Agent från aktuell main.
- [ ] **F-38.A2:** Samtidiga claimförsök skapar högst en aktiv epic och samma Coordinatorägarskap.
- [ ] **F-38.A3:** Ej uppfyllt beroende, olöst runtimegräns eller tom kandidatlista ger väntan/blockerare utan start.

**Verifiering:** Serviceprov med olika epicgrafer och parallella claim; verkligt Coordinator-startprov.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-39 Slutgranska epic och återför korrigeringskrav

**Epic/fas/prioritet:** E-10 / 10 / P1. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `0657116a-4cf9-483f-aa9f-57612d7cee39`.

**Körbar:** Nej — invänta F-38 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§25–26, 31, 44; W §§30–32. **Berör:** Coordinator-review, epicreviewhistorik, korrigerande tasks.

**Beroenden:** F-38. **Externa förutsättningar:** X-02: verifierad create/reopen-förmåga behövs för automatisk korrigering; annars används tydlig Attention.

**Arbetsinstruktion för Codex:** Implementera strukturerad epicreview med krav, acceptans, taskresultat, diff mot main och tester. Spara EPIC_APPROVED eller EPIC_CHANGES_REQUESTED från Coordinator. Vid korrigering återför kontrollen till samma Integration Agent och länka nya eller återöppnade tasks till beslutet.

**Resultat och kontrakt:** Epicreview binds till epic-SHA, main-SHA, taskmedlemskap och acceptansversion. Korrigeringsarbete har egna commits/reviews och spårbar runhistorik; tidigare leveransbevis skrivs inte över. Nya tasks skapas via verifierad TeamPlayer-förmåga eller extern åtgärd och får inte fabriceras som existerande.

**Acceptans**

- [ ] **F-39.A1:** Coordinator kan underkänna epic med konkreta kriterier och Integration Agent kan genomföra kopplad korrigering.
- [ ] **F-39.A2:** Ny epicreview bedömer uppdaterad kod/tasklista och bevarar tidigare beslut.
- [ ] **F-39.A3:** Integration/Worker kan inte godkänna epicen för main; ändrad main gör gamla approval obrukbara.

**Verifiering:** Serviceprov och verkligt korrigeringsscenario med före/efter-review och tasklänkar.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-40 Integrera epic och fortsätt efter slutverifiering

**Epic/fas/prioritet:** E-10 / 10 / P0. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `4073e26d-b095-47e3-99a6-7704f757f485`.

**Körbar:** Nej — invänta F-39 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§25–27, 30, 44; W §§33–34, 40. **Berör:** Epicservice, Git Manager, TeamPlayer, Coordinatorloop.

**Beroenden:** F-39. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Koppla aktuell Coordinator-approval till epicmerge. Kör slutverifiering på main, registrera merge/testunderlag och skriv Epic Done. Frigör epicreservation och välj nästa körbara epic först när verifieringen lyckats.

**Resultat och kontrakt:** Git Manager verifierar alla mergevillkor från F-08 igen. Nätfel efter main-merge återförsöker bara synk. Fel efter merge visar faktiska commits och lämnar epicen ej Done; ingen automatisk reset eller fortsatt beroende epic. Coordinator utför inte Worker-implementation.

**Acceptans**

- [ ] **F-40.A1:** Godkänd epic får en main-merge och Epic Done först efter godkänd slutverifiering.
- [ ] **F-40.A2:** Nästa epic använder nya main-SHA och tidigare Integration-runtime avslutas enligt policy.
- [ ] **F-40.A3:** Ändrad main, sluttestfel eller upprepat mergeanrop ger inte dubbla merges eller för tidigt nästa epic.

**Verifiering:** Temporärt Git med två epics och fel efter merge/test/synk samt verkligt begränsat Coordinatorprov.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-41 Verifiera första kompletta autonoma epicflödet

**Epic/fas/prioritet:** E-10 / 10 / P1. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `dc32d701-aca3-4f3d-a0fa-8a8e0e5457bb`.

**Körbar:** Nej — invänta F-40 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§44, 47–48; W §§5–34, 41–42. **Berör:** End-to-end-prov, MVP-acceptans.

**Beroenden:** F-40. **Externa förutsättningar:** X-01, X-02 och godkända runtimegränser; separata små testepics.

**Arbetsinstruktion för Codex:** Kör målscenariot med en epic, tre tasks och två Workers från TeamPlayer-val till main-merge. Inkludera taskkorrigering och en epicändringsbegäran; lägg en liten efterföljande epic för att verifiera fortsatt loop. Samla en sammanhängande tidslinje.

**Resultat och kontrakt:** Endast en epic är aktiv åt gången och varje leverans går genom rätt roll. Autonomi omfattar scheduling/review/merge; extern input hanteras i Attention. Själva produktbackloggens tasks ersätts inte av testepicens ID:n.

**Acceptans**

- [ ] **F-41.A1:** Tre tasks granskas, integreras och slutgranskad epic mergeas till main utan manuell scheduling/merge.
- [ ] **F-41.A2:** Task- och epickorrigering återgår till rätt agenter och kräver nya aktuella godkännanden.
- [ ] **F-41.A3:** Nästa epic startar från uppdaterad main; workergräns, rollgränser och Donevillkor kan bevisas.

**Verifiering:** Verkligt Herdr/Codex/Git/TeamPlayer-prov med oberoende kontroll av Git, tester, roller och tidslinje.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

## Epic E-11 Återhämta körningar efter avbrott och omstart

**Fas:** 11. **Prioritet:** P1. **Kanban-status:** Planned. **TeamPlayer Epic-ID:** `c1a21c17-3fcf-4f15-800d-c69f61d07df8`.

**Körbar:** Nej — E-10 Done på main; externa villkor anges per task. **Beroende:** E-10 Done på main.

**Källa:** A §§11–14, 28, 45; W §§17–18, 23–25, 35–40.

### Resultat och omfattning

Operatören kan starta om orchestratorn och återuppta verifierbart arbete utan dubbla sessioner, förlorade beslut eller felaktiga Done-statusar.

**Ingår:** Startavstämning, återanslutning, in-flight-operationer, väntande synk och kontrollerade avbrottsprov. **Utanför:** Automatiskt skapande av ersättningsworktree eller gissad återskapning av förlorad kod/session.

**Gemensamma regler:** tillämpa samtliga sex kontrakt ovan och de ingående taskernas förvillkor. **Risk och beslutspunkt:** SQLite, Git, Herdr och Kanban kan beskriva olika lägen. F-42 ger en explicit avstämningsrapport innan F-43/F-44 återupptar sidoeffekter.

### Tasks och beroenden

| Task | Leverans | Taskberoenden utöver epicens beroende | Prioritet |
| --- | --- | --- | --- |
| F-42 | Stäm av SQLite Git Herdr och TeamPlayer vid start | E-10 | P0 |
| F-43 | Återuppta verifierade sessioner och parkerade tasks | F-42 | P0 |
| F-44 | Återställ halvfärdiga starter merges och synkskrivningar | F-43 | P0 |
| F-45 | Verifiera återhämtning genom avsiktliga avbrott | F-44 | P1 |

### Epicacceptans

- [ ] **E-11.A1:** Omstart återfinner fungerande runs och återupptar rätt session/övervakning.
- [ ] **E-11.A2:** Halvfärdig start, merge och synk slutförs eller eskaleras utan dubbel operation.
- [ ] **E-11.A3:** Saknade/avvikande resurser ger konkret Attention och avbrottsprov bevarar arbete.

Epicen följer dessutom den gemensamma definitionen av Done. Acceptansen verifieras genom taskernas underlag och ett samlat prov av epicens resultat.

### Task F-42 Stäm av SQLite Git Herdr och TeamPlayer vid start

**Epic/fas/prioritet:** E-11 / 11 / P0. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `995ac9d9-31d6-4e87-ba5b-d8524fb2b298`.

**Körbar:** Nej — invänta E-10 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§11–14, 28, 45; W §§35–40. **Berör:** Recoveryservice, runtimeinventering, statusrapport.

**Beroenden:** E-10. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera en läsande reconciliation av alla oavslutade runs, reservationer och väntande operationer innan ny scheduling aktiveras. Jämför beständiga referenser med Git, Herdr/sessionstatus och TeamPlayer; klassificera verifierad, återupptagbar eller avvikande körning.

**Resultat och kontrakt:** Avstämningen får inte härleda färdig kod från Kanban eller skapa ny session för att dölja avvikelse. Extern tjänst som är otillgänglig ger osäker status och pausad berörd automation. Rapporten anger observerade ID:n/commits, orsak och nästa tillåtna åtgärd.

**Acceptans**

- [ ] **F-42.A1:** En komplett matchande run identifieras med samma worktree, branch, session och state.
- [ ] **F-42.A2:** Saknat worktree, annan HEAD eller Kanban Done utan mergeunderlag ger konkret avvikelse.
- [ ] **F-42.A3:** Herdr/TeamPlayer-nätfel stoppar ny osäker scheduling men ändrar inte Git eller bevisar förlust av session.

**Verifiering:** Fixturekombinationer av SQLite, Git, runtime och Kanban inklusive otillgängliga adaptrar.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-43 Återuppta verifierade sessioner och parkerade tasks

**Epic/fas/prioritet:** E-11 / 11 / P0. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `bb3488c7-5c64-48ac-b105-72036fda275e`.

**Körbar:** Nej — invänta F-42 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§7, 23, 28, 45; W §§17–18, 38. **Berör:** Recovery, Herdr/resume, reservationsåterställning.

**Beroenden:** F-42. **Externa förutsättningar:** X-01 och X-02 för verkliga återanslutningsprov.

**Arbetsinstruktion för Codex:** Återanslut verifierade Coordinator-, Integration- och Worker-sessioner och återställ övervakning. Bevara parkerade tasks och sparade svar; använd samma slotkontroll vid resume. Hantera saknad anslutning separat från förlorad sessionsdata.

**Resultat och kontrakt:** Kan runtime återuppta beständig Codex-session används samma sessionidentitet. Annars krävs Attention med explicit åtgärd. Ingen ny Worker tilldelas förrän ägarskap/inaktivitet är fastställt. Parkerat arbete återupptas bara med nödvändig input och ledig slot.

**Acceptans**

- [ ] **F-43.A1:** Omstart under arbete återansluter samma sessioner utan fler agents/runs.
- [ ] **F-43.A2:** PARKED och väntande svar finns kvar efter omstart och följer workergränsen vid resume.
- [ ] **F-43.A3:** Ej återupptagbar session ger Attention och inget automatiskt ersättningsworktree.

**Verifiering:** Recoveryintegration med verklig anslutningsförlust och kontrollerat borttagen sessionsreferens.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-44 Återställ halvfärdiga starter merges och synkskrivningar

**Epic/fas/prioritet:** E-11 / 11 / P0. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `6c45d324-00ea-473e-822b-9d31947efc75`.

**Körbar:** Nej — invänta F-43 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§28–30, 45; W §§23–26, 33, 40. **Berör:** Operationsjournal, Git, TeamPlayer, slotstate.

**Beroenden:** F-43. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Implementera återställning för avbrott före/efter varje start-, merge-, test- och synksteg. Återfinn skapade resurser och merge-SHA, avgör vilket verifieringssteg som saknas och fortsätt endast den säkert återförsökbara delen. Kontrollera reservationer mot faktisk runtime.

**Resultat och kontrakt:** Git-merge som redan är utförd upprepas inte. Testunderlag utan säkrad commitkoppling körs om. Okänt externt utfört steg avstäms eller eskaleras. Manuella Git/Kanbanändringar skrivs inte över utan verifiering; cleanup påverkar inte osäkrat arbete.

**Acceptans**

- [ ] **F-44.A1:** Avbrott efter skapad session men före lokal slutregistrering återfinner ägd session eller eskalerar utan dubbel start.
- [ ] **F-44.A2:** Avbrott efter task/main-merge återfinner merge-SHA och återupptar verifiering/statussynk utan ny merge.
- [ ] **F-44.A3:** Okänt kommentarutfall och avvikande worktree ger avstämning/Attention i stället för dubbla sidoeffekter.

**Verifiering:** Felinjicering vid dokumenterade operationsgränser i temporärt Git/SQLite och adaptrar med okända svar.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-45 Verifiera återhämtning genom avsiktliga avbrott

**Epic/fas/prioritet:** E-11 / 11 / P1. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `da58688a-6542-49f4-be40-8df908a08ee0`.

**Körbar:** Nej — invänta F-44 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§28, 45; W §§17–18, 35–40. **Berör:** Avbrottsmatris, verkliga integrationer, runbook.

**Beroenden:** F-44. **Externa förutsättningar:** X-03: isolerad drift/testmaskin där process- och maskinomstart får provas; X-01 och X-02.

**Arbetsinstruktion för Codex:** Kör avbrottsprov för dödad orchestrator, tappad SSH, omstartad Herdr, terminerad Codex och omstartad målmaskin. Dokumentera återställning av arbete, parkering, reviews, merges och synk. Pi-omstart körs om Pi är vald driftmiljö; annars anges faktisk målmaskin.

**Resultat och kontrakt:** Avbrott görs i ofarlig testmiljö. Varje scenario definierar startläge, avbrottspunkt, förväntad fortsättning eller Attention och bevis efter återstart. En simulerad processdöd räknas inte som verifierad maskinomstart.

**Acceptans**

- [ ] **F-45.A1:** Verklig tjänst/Herdr/session-omstart återupptar verifierbart arbete utan dubbla Workers eller merges.
- [ ] **F-45.A2:** Borttagen resurs ger begriplig Attention och sparat arbete/logghistorik bevaras.
- [ ] **F-45.A3:** Samtliga avbrottstyper har resultat och återställningssteg; målmaskinomstart verifieras i vald miljö.

**Verifiering:** Verklig avbrottsmatris plus kontroll av runs, sessions-ID, commits och synkavsikter efter omstart.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

## Epic E-12 Härda orchestrering och gör drift spårbar

**Fas:** 12. **Prioritet:** P0. **Kanban-status:** Planned. **TeamPlayer Epic-ID:** `be200a7c-4dad-47a1-a785-e25e84903124`.

**Körbar:** Nej — E-11 Done på main; externa villkor anges per task. **Beroende:** E-11 Done på main.

**Källa:** A §§8, 29–33, 46, 49–50; W §§3–4, 23–26, 33, 39–40.

### Resultat och omfattning

Operatören kan köra och felsöka orchestratorn med verifierade locks, tidsgränser, retries, mergevillkor, audit och återställningsinstruktioner.

**Ingår:** Fördjupad samtidighetskontroll, deadlines, idempotens, Git/policykontroller, runtime/auditlogg och driftacceptans. **Utanför:** Nya produktfunktioner utanför MVP, fler repositories/epics eller dynamisk scaling.

**Gemensamma regler:** tillämpa samtliga sex kontrakt ovan och de ingående taskernas förvillkor. **Risk och beslutspunkt:** Stale locks och okända externa resultat kan leda till dubbla ägare. Härdning ska bygga vidare på tidigare skydd utan att automatiskt frigöra resurser enbart efter tid.

### Tasks och beroenden

| Task | Leverans | Taskberoenden utöver epicens beroende | Prioritet |
| --- | --- | --- | --- |
| F-46 | Härda project epic och task locks över processgränser | E-11 | P0 |
| F-47 | Inför deadlines begränsade retries och strukturerade fel | F-46 | P0 |
| F-48 | Verifiera idempotens och Git skydd vid konkurrerande ändringar | F-47 | P0 |
| F-49 | Gör runtime och auditlogg tillräckliga för felsökning | F-48 | P1 |
| F-50 | Verifiera robust drift och dokumentera återställning | F-49 | P1 |

### Epicacceptans

- [ ] **E-12.A1:** Flera konkurrerande processer kan inte äga samma project/epic/task eller mergea stale underlag.
- [ ] **E-12.A2:** Timeouts, retries och okända svar leder till spårbar recovery eller Attention utan dubbel sidoeffekt.
- [ ] **E-12.A3:** Operatören kan följa hela beslutskedjan och återställa bevarat arbete enligt verifierad runbook.

Epicen följer dessutom den gemensamma definitionen av Done. Acceptansen verifieras genom taskernas underlag och ett samlat prov av epicens resultat.

### Task F-46 Härda project epic och task locks över processgränser

**Epic/fas/prioritet:** E-12 / 12 / P0. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `831d353a-a683-452f-864d-823b5908cd85`.

**Körbar:** Nej — invänta E-11 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§29–30, 46; W §§4, 8, 23, 40. **Berör:** Locks, SQLite, leases, claim/mergepolicy.

**Beroenden:** E-11. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Härda befintliga claims och reservationer för flera orchestratorprocesser. Implementera dokumenterad lockordning, ägartoken och säker återtagning efter avstämning. Skydda epicintegration och mainintegration med rätt granularitet.

**Resultat och kontrakt:** Project låser aktiv epic/Coordinator, epic låser integrationsägare/merge och task låser run/slot. Föråldrad ägare får inte fortsätta utföra skrivningar efter återtagning. Tidens gång ensam bevisar inte att en Worker eller Git-operation är inaktiv.

**Acceptans**

- [ ] **F-46.A1:** Två processer som claimer samma project/epic/task får en enda giltig ägare.
- [ ] **F-46.A2:** Processkrasch kan återställas utan att aktiv ägd runtime startas dubbelt.
- [ ] **F-46.A3:** Gammal ägartoken och omvänd lockordning avvisas eller hanteras utan deadlock.

**Verifiering:** Flera subprocesser mot samma temporära SQLite/repository med krasch, långsam operation och stale owner.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-47 Inför deadlines begränsade retries och strukturerade fel

**Epic/fas/prioritet:** E-12 / 12 / P0. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `cf9a3d67-b839-45a1-88ed-023369d7e3e5`.

**Körbar:** Nej — invänta F-46 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§7, 30, 46; W §§16, 22, 40. **Berör:** Adaptrar, processhantering, felkontrakt.

**Beroenden:** F-46. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Definiera tidsgränser för start, bekräftelse, tester, reviewtransport, Git och extern synk. Klassificera permanent fel, transient fel och okänt utfört resultat. Inför begränsade retries med fördröjning och observerbar eskalering.

**Resultat och kontrakt:** Återförsök av muterande steg kräver verifierad idempotens eller avstämning. Timeout stoppar inte per automatik en extern session och frigör inte en slot. Testprocesser och adapterprocesser får tydlig hantering för avbrytning och kvarvarande barnprocesser.

**Acceptans**

- [ ] **F-47.A1:** Transienta läsfel återförsöks enligt budget medan permanenta policyfel inte loopar.
- [ ] **F-47.A2:** Timeout efter möjlig sessionstart eller merge avstäms innan nytt muterande försök.
- [ ] **F-47.A3:** Uttömd retrybudget ger strukturerat fel/Attention med operation, run och konkret nästa steg.

**Verifiering:** Kontrollerad klocka/transport och subprocess som hänger, avslutas sent eller lämnar okänt utfall.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-48 Verifiera idempotens och Git skydd vid konkurrerande ändringar

**Epic/fas/prioritet:** E-12 / 12 / P0. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `fc88a25c-6fdc-468d-a817-145b88ccec19`.

**Körbar:** Nej — invänta F-47 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§8–9, 27, 29–30, 46, 50; W §§23–25, 33, 40. **Berör:** Git Manager, operationer, roll/path/commitpolicy.

**Beroenden:** F-47. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Härda idempotensnycklar, branch/pathkontroller och commitvillkor i samtliga kritiska operationer. Prova ändrat HEAD mellan review och merge, främmande repository, osäkra paths, dubbla anrop och avbrott runt Git. Dokumentera gränser för externa manuella Gitändringar.

**Resultat och kontrakt:** Endast tilldelad roll/run kan utföra operationen. Förväntad HEAD kontrolleras precis vid skyddad ändring; förändring leder till ny verifiering. Ingen automatisk destructive reset, epic-konfliktlösning eller radering av osäkrat arbete. Externa skrivare ska upptäckas och stoppa berörd automation.

**Acceptans**

- [ ] **F-48.A1:** Dubbla start/merge/resume/cleanup/synkanrop ger samma kända resultat utan dubbla sidoeffekter.
- [ ] **F-48.A2:** HEAD-race, smutsigt worktree, symlink/path-avvikelse och fel merge-riktning stoppar osäker ändring.
- [ ] **F-48.A3:** Worker och Integration kan inte få main-merge genom ändrade argument eller återanvänd ägartoken.

**Verifiering:** Konkurrerande Git/subprocess-prov och negativa policyprov med faktisk operationsjournal.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-49 Gör runtime och auditlogg tillräckliga för felsökning

**Epic/fas/prioritet:** E-12 / 12 / P1. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `a57a04a3-af85-4742-b93e-b4591906915a`.

**Körbar:** Nej — invänta F-48 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§32–33, 46; W §§16, 19–20, 30–33, 39. **Berör:** Strukturerade loggar, audit, MCP-status, export.

**Beroenden:** F-48. **Externa förutsättningar:** Inga utöver projektets grundförutsättningar.

**Arbetsinstruktion för Codex:** Utöka befintlig loggning med separat audit för val av epic, tasktilldelning, Attention, reviewbeslut, merge-SHA och epicgodkännande. Exponera status och väntande operationer för rätt roll; dokumentera retention och sanerad export för felsökning.

**Resultat och kontrakt:** Audit innehåller project/epic/task/run, aktör, operation, tid, relevanta commits och resultat. Credentials och råa hemligheter från prompts ska inte loggas. Runtime-logg förklarar felsituationer; beständiga beslut skrivs tillsammans med motsvarande state där möjligt.

**Acceptans**

- [ ] **F-49.A1:** En provleverans kan följas från epicval till main-merge med alla review- och blockerarbeslut.
- [ ] **F-49.A2:** Efter omstart finns audit och väntande synkskrivningar kvar med korrekta kopplingar.
- [ ] **F-49.A3:** Sanerad export och runtime-status avslöjar inte testcredentials eller data från obehörigt projekt.

**Verifiering:** Granska tidslinje från verkligt prov och kontrollera sekretess med planterade testhemligheter.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

### Task F-50 Verifiera robust drift och dokumentera återställning

**Epic/fas/prioritet:** E-12 / 12 / P1. **Kanban-status:** Planned. **TeamPlayer Task-ID:** `98fb1a30-c456-4e69-b45d-b0778dd84e66`.

**Körbar:** Nej — invänta F-49 och epicens beroende samt nedanstående externa villkor.

**Källa:** A §§28–33, 45–50; W §§25–26, 33, 38–40. **Berör:** Drift/runbook, backup/restore, slutacceptans.

**Beroenden:** F-49. **Externa förutsättningar:** X-01, X-02 och X-03; vald driftmiljö och säker testbackup.

**Arbetsinstruktion för Codex:** Dokumentera installation, uppgradering, kontrollerat stopp, statebackup, retention, recovery och Attention. Verifiera återställning av SQLite tillsammans med nödvändiga Git/worktree- och runtimekopplingar. Kör slutlig MVP- och hardeningmatris med alla tidigare grindar.

**Resultat och kontrakt:** Backup tas konsistent vid pausad/kontrollerad state och beskriver SQLite/WAL om relevant, Git/worktrees och externa referenser. Credentials återställs separat. Förlorad extern session efter restore kan kräva Attention; backup får inte beskrivas som garanti att runtime alltid kan återupptas. Godkänd drift kräver E-11-avbrottsprov.

**Acceptans**

- [ ] **F-50.A1:** Operatören kan följa runbook för start/stopp och återställning till verifierbar state utan förlorat committat arbete.
- [ ] **F-50.A2:** Slutprov med tre tasks, två Workers, Attention, korrigering, main-merge och omstart uppfyller tidigare acceptans.
- [ ] **F-50.A3:** Konkurrerande processer, nätfel och stale review stoppar osäker automation; kända begränsningar och återstående åtgärder är dokumenterade.

**Verifiering:** Praktiskt restoreprov och samlad riskbaserad acceptansmatris med versions- och miljöuppgifter.

**Leverans:** tillämpa den gemensamma taskdefinitionen av Done. Worker lämnar READY_FOR_REVIEW med commit och underlag; Integration-rollen registrerar review, merge-SHA och integrationsresultat innan Done.

## Kravtäckning

Tabellen kopplar tvärgående krav till leveranser. Samtliga faser 1–12 täcks av motsvarande epic.

| Kravområde | Kravkälla | Leveranser |
| --- | --- | --- |
| Deterministisk orchestrator och lokalt MCP | A §§2, 4–5, 50; W §§4, 40 | F-01, F-04, F-15, F-35, F-38 |
| EpicRun, TaskRun, Review och interna tillstånd | A §§11–16; W §§35–37 | F-02–F-03, F-16, F-20, F-39 |
| Git, worktrees, synk och merge | A §§8–10, 27, 36; W §§3–4, 23–26, 33 | F-05–F-09, F-18, F-21, F-40, F-48 |
| Herdr/Codex-livscykel och Worker-policy | A §§7, 19–20, 31, 37–38; W §§10–12, 19, 38 | F-10–F-17 |
| Taskreview, feedback och Done | A §§20–22, 39; W §§20–25 | F-18–F-22 |
| TeamPlayer-läsning, status och ansvar | A §§6, 16, 40; W §§5, 9, 13–18, 39 | F-23–F-26 |
| Två Workers och beroenden | A §§17–18, 41; W §§8–10, 27–28 | F-27–F-30 |
| Attention, parkering och samma-session-resume | A §§23, 42; W §§16–18, 22 | F-31–F-33 |
| Långlivad Integration Agent och epicöverlämning | A §§4.2, 24, 43; W §§7, 29–30 | F-34–F-37 |
| Coordinator, slutreview, korrigering och nästa epic | A §§4.1, 25–27, 44; W §§5–6, 30–34 | F-38–F-41 |
| Recovery vid alla angivna avbrott | A §§28, 45; W §§17–18, 38–40 | F-42–F-45 |
| Locks, idempotens, deadlines, retries och skydd | A §§29–30, 46; W §§23–25, 33, 40 | F-15, F-25, F-28, F-46–F-48 |
| Runtime-/auditlogg och operatörsunderlag | A §§32–33, 46; W §§19–20, 30–33 | F-01, F-02, F-49–F-50 |
| MVP och första kompletta scenario | A §§47–48; W §§41–42 | F-30, F-33, F-37, F-41, F-50 |

## Nästa steg

E-03 är Done efter PR #3/main-merge 0395728 och 262 sluttester. Fortsätt direkt E-04/F-14 från uppdaterad main. Leveransordningen är oförändrad.

## Kanbanöversikt

**Statuskälla:** TeamPlayer HerdrCoordinator, avstämt 2026-10-08 efter E-01-main-merge och F-05–F-09-integration. Taskstatus Pending motsvarar Planned. Epicstatus är återläst i TeamPlayer: E-01–E-05 Done, E06 Active och E07–E12 Pending/Planned. Epics använder endast Planned/Active/Done. Verifierat räknar endast implementationsacceptans; skapade TeamPlayer-uppgifter bockar inte av dessa kriterier. Den gemensamma definitionen av Done krävs dessutom. Ordningen nedan är planerad leveransordning, med epicen före dess tasks.

| Ordning | ID | Typ | Namn | Epic | Fas | TeamPlayer-ID | Prioritet | Kanban-status | Körbar | Verifierat | Beroende eller blockerare | Nästa steg |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | E-01 | Epic | Starta orchestratorn och bevara körningarnas tillstånd | — | 1 | 0da5c7c4-6e29-475e-aeb6-ce887f3864db | P0 | Done | — | 3/3 | Inget | PR #1, main-merge 9cafb75 och 64 sluttester passerar; fortsätt E-02/F-05. |
| 2 | F-01 | Task/feature | Starta ett konfigurerbart Python-projekt | E-01 | 1 | 9bc95f85-f05d-4842-b517-c1f8132c49ab | P0 | Done | — | 3/3 | Inget | Integrerad i E-01 via 7dc5d80; fortsätt med F-02. |
| 3 | F-02 | Task/feature | Spara runs och reviewhistorik i SQLite | E-01 | 1 | b555e011-5e55-4cae-8d14-9cdd57725e5c | P0 | Done | — | 3/3 | F-01 | Integrerad via 8e29ef2; fortsätt med F-03. |
| 4 | F-03 | Task/feature | Validera task och epic genom explicita tillstånd | E-01 | 1 | 6ca76316-bff1-40e6-b57d-dd6407e449dd | P0 | Done | — | 3/3 | F-02 | Integrerad via 904a022; fortsätt med F-04. |
| 5 | F-04 | Task/feature | Exponera lokala MCP-kontrakt med betrodda roller | E-01 | 1 | 96cc0f5f-e737-4a19-897f-2419f690b2e0 | P0 | Done | — | 3/3 | F-03 | Integrerad via d37fcfd; E-01 slutgranskas. |
| 6 | E-02 | Epic | Isolera och integrera arbete genom Git worktrees | — | 2 | 225cca70-7f09-4313-a020-52c8b0b7b069 | P0 | Done | — | 3/3 | E-01 Done på main | PR #2, main-merge 9beaf34 och 187 sluttester passerar; fortsätt E-03/F-10. |
| 7 | F-05 | Task/feature | Skapa och återfinn epic och task worktrees | E-02 | 2 | 9974ec4e-453c-4498-8994-f14e119c6e2d | P0 | Done | — | 3/3 | E-01 | Granskad och integrerad via 5efca19; 88 integrationstester passerar. Fortsätt F-06. |
| 8 | F-06 | Task/feature | Leverera diff och aktuella Git fakta för granskning | E-02 | 2 | 56e322eb-8902-4e39-92f0-66ed47224ee7 | P0 | Done | — | 3/3 | E-01, F-05 | Granskad och integrerad via aa373bd; 119 integrationstester passerar. Fortsätt F-07. |
| 9 | F-07 | Task/feature | Synkronisera task mot epic och integrera granskad task | E-02 | 2 | 4bc70580-208a-4c06-a18a-2adce002a5f7 | P0 | Done | — | 3/3 | E-01, F-06 | Granskad och integrerad via a6a5953; 141 tester passerar. Fortsätt F-08. |
| 10 | F-08 | Task/feature | Integrera godkänd epic till aktuell main | E-02 | 2 | a9c700e9-6200-4aa7-a19f-3b1535270e57 | P0 | Done | — | 3/3 | E-01, F-07 | Granskad och integrerad via 1d6e2be; 163 tester passerar. Fortsätt F-09. |
| 11 | F-09 | Task/feature | Avsluta Git resurser efter verifierad leverans | E-02 | 2 | 96091ef5-be75-4d81-af5f-ffceda85b50c | P0 | Done | — | 3/3 | E-01, F-07, F-08 | Granskad och integrerad via 65097a8; 187 tester passerar. E-02 slutgranskas. |
| 12 | E-03 | Epic | Starta och återanslut agentruntime genom Herdr | — | 3 | 3b52b7d6-7527-4d44-a873-238f26067246 | P1 | Done | — | 3/3 | E-02 Done på main | PR #3/main 0395728 och 262 sluttester passerar; fortsätt E-04/F-14. |
| 13 | F-10 | Task/feature | Verifiera Herdr och Codex gränssnitt | E-03 | 3 | fd25939b-3ae9-4215-8edc-dd3415a696b5 | P1 | Done | — | 3/3 | E-02, X-01 | Granskad/integrerad via 7dfc54b; dokument-/provlänkkontroller passerar. Fortsätt F-11. |
| 14 | F-11 | Task/feature | Skapa workspace och starta Codex i rätt worktree | E-03 | 3 | 784fbbcf-dcf5-475d-940f-bb4039cf40fe | P1 | Done | — | 3/3 | E-02, F-10, X-01 | Granskad/integrerad via 4c9956ac; 213 sluttester passerar. Fortsätt F-12. |
| 15 | F-12 | Task/feature | Skicka uppdrag och observera start och status | E-03 | 3 | 8f1c732d-8ada-4e39-b2f1-b140f2795517 | P1 | Done | — | 3/3 | E-02, F-11, X-01 | Granskad/integrerad via 06d64898; 240 sluttester passerar. |
| 16 | F-13 | Task/feature | Återanslut och stoppa registrerad runtime | E-03 | 3 | 83a64bd8-6bfd-4988-82dd-40f3aca590a5 | P1 | Done | — | 3/3 | E-02, F-12, X-01 | Granskad/integrerad via 1a16aea9; 262 sluttester passerar. |
| 17 | E-04 | Epic | Låt en Worker leverera en verifierbar task | — | 4 | 242ffa18-da4e-4496-8e9f-c4c1b4d8315c | P1 | Done | Ja | 3/3 | E-03 Done på main | PR #4/main56c5aff; 365 sluttester/gates, egenacceptans3/3 och TeamPlayer Done. |
| 18 | F-14 | Task/feature | Beskriv ett Worker uppdrag och rapportkontrakt | E-04 | 4 | 10bfff5f-ba3a-4f10-8510-f8578da7a747 | P1 | Done | — | 3/3 | E-03 | Granskad/integrerad via 258010c5; 297 regressionstester passerar. |
| 19 | F-15 | Task/feature | Starta en explicit task med en Worker | E-04 | 4 | 1d8feb08-94fe-42a1-a2ca-fefba83cc40d | P0 | Done | — | 3/3 | E-03, F-14 | Granskad/integrerad via a61f181d; 329 regressionstester passerar. |
| 20 | F-16 | Task/feature | Verifiera Worker rapport mot committat arbete | E-04 | 4 | 472fbfc1-4e51-4eba-bfbe-490edb090546 | P0 | Done | — | 3/3 | E-03, F-15 | Granskad/integrerad via 575c8efa; 361 regressionstester passerar. |
| 21 | F-17 | Task/feature | Verifiera en verklig Worker leverans | E-04 | 4 | 9ae60f57-9194-4bf8-8763-d56cddfdc868 | P1 | Done | Ja | 3/3 | E-03, F-16, X-01 | APPROVED 7e0f8bb mot a73db1d; merge fe3eb71; 365 tester och gates; verkligt Worker/policyprov. F38 runtimegate dokumenterad. |
| 22 | E-05 | Epic | Granska korrigera och integrera en task | — | 5 | 9d4ff24d-71a6-4e02-9498-bbbaee7e1db8 | P1 | Done | Nej | 3/3 | E-04 Done på main | PR#5/main9d16c7f, slutverifierad; docs/reviews/E-05.md. |
| 23 | F-18 | Task/feature | Bygg komplett reviewkontext från aktuell epic | E-05 | 5 | 6dfd789c-25d6-465b-bb75-a81e4b5d4a34 | P0 | Done | Nej | 3/3 | E-04 | Review 2129e00, merge b2410b7, 397 passed (380.33 s); se docs/reviews/F-18.md. |
| 24 | F-19 | Task/feature | Återför konkret reviewfeedback till samma Worker | E-05 | 5 | 9deec5f8-e93d-4b71-9996-956049931e18 | P1 | Done | Nej | 3/3 | E-04, F-18, X-01 | Review a049fef, merge 22ea85d, 424 passed (460.14 s); native fix/prov och docs/reviews/F-19.md. |
| 25 | F-20 | Task/feature | Bind taskgodkännande till granskat underlag | E-05 | 5 | 5de9046f-4c8b-4f69-ae12-33fc5d213df3 | P0 | Done | Nej | 3/3 | E-04, F-19 | Review e7e917c, merge 9d6cadb, 445 passed (540.48 s); docs/reviews/F-20.md. |
| 26 | F-21 | Task/feature | Sätt task Done efter merge och integrationstester | E-05 | 5 | e931598a-4fa5-488e-aeea-0db91a570bdd | P0 | Done | Nej | 3/3 | E-04, F-20 | Review 9a064d0, merge 4486d10, 469 passed (957.77 s), exit0; docs/reviews/F-21.md. |
| 27 | F-22 | Task/feature | Verifiera review och fix till integrerad task | E-05 | 5 | 875b4e3d-e3de-40ae-bb76-6aee5e8705c2 | P1 | Done | Nej | 3/3 | E-04, F-21, X-01 | Review7f5b148/merge944f865, nativeflow +test23,10CLIpass; docs/reviews/F-22.md. |
| 28 | E-06 | Epic | Spegla arbetsflödet i TeamPlayer | — | 6 | e779c93c-7f75-43e0-97ed-53373bb0fd66 | P1 | Done | Nej | 3/3 | E-05 Done på main | PR#6/main54e346d;588aggregate/205final PASS; docs/reviews/E-06.md. |
| 29 | F-23 | Task/feature | Verifiera TeamPlayer projekt och MCP kontrakt | E-06 | 6 | 6dd3f8a2-cfcf-4483-a124-944804a5f65f | P1 | Done | Nej | 3/3 | E-05, X-02 | Review8bc2517/merge173b7f6;23tests8.24s/nativeMCP PASS; docs/reviews/F-23.md. |
| 30 | F-24 | Task/feature | Läs epics tasks och beroenden till domänmodellen | E-06 | 6 | 7a9a311f-5f20-4247-9ef7-a5e5c57e39bc | P1 | Done | Nej | 3/3 | E-05, F-23, X-02 | Review4692b8e/mergeeef679e;113tests14.01s +nativeReaderPASS; docs/reviews/F-24.md. |
| 31 | F-25 | Task/feature | Synkronisera status och kommentarer utan nya sidoeffekter | E-06 | 6 | 7ab4a0ac-405d-4905-bc46-a2d5f0431c86 | P0 | Done | Nej | 3/3 | E-05, F-24, X-02 | Review589631e/slutmergede46d0f;204tests+12alias83.58s/nativePASS; docs/reviews/F-25.md. |
| 32 | F-26 | Task/feature | Verifiera TeamPlayer kopplingen på en testepic | E-06 | 6 | 1626d7a9-387d-47cd-b20b-86cb2a9f0613 | P1 | Done | Nej | 3/3 | E-05, F-25, X-01, X-02 | Review918bb1d/merge6d6ebd9;nativeflöde/stängtMCP/20CLI6.98s PASS; docs/reviews/F-26.md. |
| 33 | E-07 | Epic | Genomför beroendestyrda tasks med två Workers | — | 7 | 75285fe5-e9bb-46ea-b5fd-19135c40166d | P1 | Done | Nej | 3/3 | E-06 Done på main | PR7/main9ff5eb8;692 samlade/119 sluttester PASS; docs/reviews/E-07.md. |
| 34 | F-27 | Task/feature | Välj endast körbara tasks i rätt beroendeordning | E-07 | 7 | 8c9e5322-28c7-4310-b444-4c3a843fb671 | P0 | Done | Nej | 3/3 | E-06 | Reviewa815f1b/merge161d3f5;172tests145.70s PASS; docs/reviews/F-27.md. |
| 35 | F-28 | Task/feature | Reservera högst två aktiva Worker slots | E-07 | 7 | 3f47197b-324f-4b02-87a7-759fc22620ce | P0 | Done | Nej | 3/3 | E-06, F-27 | Ursprunglig review/merge+CLI-korrektion7a08f25/merge9ab9252/15PASS; docs/reviews/F-28.md. |
| 36 | F-29 | Task/feature | Driv scheduling och serialisera taskintegration | E-07 | 7 | 56b76e54-d870-452a-a477-4d4d4177260f | P1 | Done | Nej | 3/3 | E-06, F-28 | Review6b59c3a/merge66f381e;103 PASS; docs/reviews/F-29.md. |
| 37 | F-30 | Task/feature | Verifiera tre tasks med två parallella Workers | E-07 | 7 | 5618ec4b-f817-4789-b45c-284e29199936 | P1 | Done | Nej | 3/3 | E-06, F-29, X-01, X-02 | Review1ad07aa/mergef267306;122PASS; native15overlap/max2/treDone; docs/reviews/F-30.md. |
| 38 | E-08 | Epic | Parkera blockerade tasks och återuppta samma arbete | — | 8 | dc637987-8dce-49ef-a178-7ed819ce6db7 | P1 | Planned | Ja | 0/3 | E-07 Done på main | E07 verifierad/Done på main; F31 nästa kandidat. |
| 39 | F-31 | Task/feature | Registrera blockerare och parkera Worker säkert | E-08 | 8 | 26abd83e-7e41-4a41-9524-c827011cbe1e | P0 | Planned | Ja | 0/3 | E-07, X-01, X-02 | Nästa prioriterade kandidat efter E07-Done/main9ff5eb8/119 sluttester; kontrollera aktuella villkor. |
| 40 | F-32 | Task/feature | Återuppta Attention med sparat beslut och ledig slot | E-08 | 8 | 41cb49c7-c172-4182-8d17-8ba2e255f07a | P0 | Planned | Nej | 0/3 | E-07, F-31, X-01, X-02 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 41 | F-33 | Task/feature | Verifiera att Attention inte stoppar andra tasks | E-08 | 8 | f7a0c038-4f49-48d0-ba09-fbca7bfe0e92 | P1 | Planned | Nej | 0/3 | E-07, F-32, X-01, X-02 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 42 | E-09 | Epic | Låt en långlivad Integration Agent driva en epic | — | 9 | fd79802a-93dd-4b77-b4b4-71ba93060ad6 | P1 | Planned | Nej | 0/3 | E-08 Done på main | Genomför ingående tasks; därefter epicacceptans och slutreview. |
| 43 | F-34 | Task/feature | Starta en enda långlivad Integration Agent per epic | E-09 | 9 | 678fb877-d9c7-4d07-a182-9ee79cc0cbdc | P0 | Planned | Nej | 0/3 | E-08, X-01, X-02 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 44 | F-35 | Task/feature | Låt Integration Agent styra tasks genom verktyg | E-09 | 9 | 7f990cb9-be35-485f-a9bc-a3ff468de448 | P1 | Planned | Nej | 0/3 | E-08, F-34, X-01, X-02 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 45 | F-36 | Task/feature | Verifiera epicen och lämna komplett reviewunderlag | E-09 | 9 | e8e489aa-0235-436a-adc0-4c61703dab9e | P0 | Planned | Nej | 0/3 | E-08, F-35 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 46 | F-37 | Task/feature | Verifiera en epic styrd av Integration Agent | E-09 | 9 | b8e3bd6d-2287-447a-9f48-bb7863101cd6 | P1 | Planned | Nej | 0/3 | E-08, F-36, X-01, X-02 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 47 | E-10 | Epic | Slutgranska integrera och välj nästa epic med Coordinator | — | 10 | 559a4c95-3282-4b4b-a30f-97f9d0dbbaea | P1 | Planned | Nej | 0/3 | E-09 Done på main | Genomför ingående tasks; därefter epicacceptans och slutreview. |
| 48 | F-38 | Task/feature | Välj och claima nästa epic med Coordinator | E-10 | 10 | 64b97b6e-2251-4964-9b67-f67d709de9bf | P0 | Planned | Nej | 0/3 | E-09, X-01, X-02 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 49 | F-39 | Task/feature | Slutgranska epic och återför korrigeringskrav | E-10 | 10 | 0657116a-4cf9-483f-aa9f-57612d7cee39 | P1 | Planned | Nej | 0/3 | E-09, F-38, X-02 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 50 | F-40 | Task/feature | Integrera epic och fortsätt efter slutverifiering | E-10 | 10 | 4073e26d-b095-47e3-99a6-7704f757f485 | P0 | Planned | Nej | 0/3 | E-09, F-39 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 51 | F-41 | Task/feature | Verifiera första kompletta autonoma epicflödet | E-10 | 10 | dc32d701-aca3-4f3d-a0fa-8a8e0e5457bb | P1 | Planned | Nej | 0/3 | E-09, F-40, X-01, X-02 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 52 | E-11 | Epic | Återhämta körningar efter avbrott och omstart | — | 11 | c1a21c17-3fcf-4f15-800d-c69f61d07df8 | P1 | Planned | Nej | 0/3 | E-10 Done på main | Genomför ingående tasks; därefter epicacceptans och slutreview. |
| 53 | F-42 | Task/feature | Stäm av SQLite Git Herdr och TeamPlayer vid start | E-11 | 11 | 995ac9d9-31d6-4e87-ba5b-d8524fb2b298 | P0 | Planned | Nej | 0/3 | E-10 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 54 | F-43 | Task/feature | Återuppta verifierade sessioner och parkerade tasks | E-11 | 11 | bb3488c7-5c64-48ac-b105-72036fda275e | P0 | Planned | Nej | 0/3 | E-10, F-42, X-01, X-02 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 55 | F-44 | Task/feature | Återställ halvfärdiga starter merges och synkskrivningar | E-11 | 11 | 6c45d324-00ea-473e-822b-9d31947efc75 | P0 | Planned | Nej | 0/3 | E-10, F-43 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 56 | F-45 | Task/feature | Verifiera återhämtning genom avsiktliga avbrott | E-11 | 11 | da58688a-6542-49f4-be40-8df908a08ee0 | P1 | Planned | Nej | 0/3 | E-10, F-44, X-01, X-02, X-03 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 57 | E-12 | Epic | Härda orchestrering och gör drift spårbar | — | 12 | be200a7c-4dad-47a1-a785-e25e84903124 | P0 | Planned | Nej | 0/3 | E-11 Done på main | Genomför ingående tasks; därefter epicacceptans och slutreview. |
| 58 | F-46 | Task/feature | Härda project epic och task locks över processgränser | E-12 | 12 | 831d353a-a683-452f-864d-823b5908cd85 | P0 | Planned | Nej | 0/3 | E-11 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 59 | F-47 | Task/feature | Inför deadlines begränsade retries och strukturerade fel | E-12 | 12 | cf9a3d67-b839-45a1-88ed-023369d7e3e5 | P0 | Planned | Nej | 0/3 | E-11, F-46 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 60 | F-48 | Task/feature | Verifiera idempotens och Git skydd vid konkurrerande ändringar | E-12 | 12 | fc88a25c-6fdc-468d-a817-145b88ccec19 | P0 | Planned | Nej | 0/3 | E-11, F-47 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 61 | F-49 | Task/feature | Gör runtime och auditlogg tillräckliga för felsökning | E-12 | 12 | a57a04a3-af85-4742-b93e-b4591906915a | P1 | Planned | Nej | 0/3 | E-11, F-48 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
| 62 | F-50 | Task/feature | Verifiera robust drift och dokumentera återställning | E-12 | 12 | 98fb1a30-c456-4e69-b45d-b0778dd84e66 | P1 | Planned | Nej | 0/3 | E-11, F-49, X-01, X-02, X-03 | Verifiera beroenden och villkor; följ taskens Codex-instruktion. |
