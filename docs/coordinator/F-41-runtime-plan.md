# F41: verifierade runtimegränser och verkligt autonomt scenario

## Beslut före implementation

F40 är Done och integrerad till E10 med verifierat native-/eftertestunderlag.
F41 behåller sina tre acceptanskriterier och levererar det kompletta scenariot.
F17:s mätning visade att legacy workspace-write lämnar extern läsning, Git-
eskalering och verifieringsprocess osäkrade. Det räcker inte att godkänna ett repo
eller returnera positiva boolar. F41 inför därför det minsta sammanhängande stöd
som behövs för dess egen säkra provkörning: ett registrerat sessionsprofilval och
en avgränsad Worker-commitservice. Dessa hör ihop; inget av dem aktiverar autonomi
ensamt. Ingen tidigare Done-feature återöppnas eller ersätts med en ny task.

Manuell implementation sker enbart i task/e10-f41. Produktens två Workers startas
endast som faktiska testkörningar. Utvecklingsworktrees adopteras aldrig.

## Sessionsprofiler

- Lägg till ett valfritt betrott startkontrakt för Codex. Standardens gamla argv,
  sandbox/on-request och fingerprint ska förbli byte-/beteendemässigt kompatibla.
- Ett uttryckligt profilval ersätter legacy --sandbox i just den egna sessionen.
  Root/Integration får läsning av egen arbetsyta, Worker skrivning i egen arbetsyta
  med Git-/agentmetadata skrivskyddad. Extern state, profiler, konfiguration och
  credentials ingår inte i filsystemsvyn. Nätverk är spärrat för lokala kommandon.
- Exakta profilregler, hjälparbinär, PATH och approvalpolicy binds till startens
  fingerprint och kontrolleras mot verkliga native processargv/session/cwd. Ny
  runtime-identitet eller konfigurationsdrift ger hinder, aldrig permissiv fallback.
- Använd endast sessionsbundna explicita konfigurationsargument. Ändra inte global
  Codex-konfiguration eller installera uppdateringar som del av provet. Okänd eller
  ej upprätthållbar profil ska stoppa starten.
- En separat begränsad lista egna MCP-verktyg får förkonfigureras för det uttryckligt
  auktoriserade provet. Root/Integration/Worker får bara sitt registrerade katalog-
  och rollscope; andra servrar/plugins stängs av och faktisk katalog verifieras.

OpenAI Docs beskriver [profiler och att legacy --sandbox inte ska blandas med dem](https://learn.chatgpt.com/docs/permissions)
och [sessionskonfiguration/per-tool approval_mode](https://learn.chatgpt.com/docs/config-file/config-reference).
Dessa källor etablerar inte att den lokala miljön redan är verifierad.

## Egen commit genom betrodd service

Worker ska kunna commitera utan skrivåtkomst till gemensam Git-metadata eller
shelleskalering. Ett valfritt registrerat worker_commit-verktyg erbjuder stabil
requestnyckel och commitmeddelande, inte fria paths, refs, roller, kommandon eller
påstådda SHA. Ett eget läsresultat kan ange aktuell branch/commit när gemensam Git
är dold i Worker-shellen.

Servicekontroller före effekt:

1. Den betrott registrerade anslutningen är Worker för exakt verklig task/run.
   Läs inte roll/ägare/session från agentargument. Knyt native stdio-klientens
   verkliga föräldraprocess till verifierad aktuell Codex-runtime/session/cwd.
2. Tasken är WORKING med unik reserverad slot i rätt aktiva epic. Verifiera F05-
   branch/path/common-dir/ägarskap och aktuell registrering/ACK/generation.
3. Lås repositoryts gemensamma integrationslås, återläs state/HEAD och kontrollera
   frånvaro av pågående Git, osäkra index-/symlinkvägar och externa filter/drivers.
4. Spara beständig taskbunden intention före staging/commit. Git-adaptern utför
   bara staging/commit av egen arbetsyta med kontrollerad miljö, utan hooks,
   signering, branchbyte, merge eller fria Git-argument.
5. Bind sparat träd, föregående commit och operationmarker till den faktiska nya
   committen. Vid tappat svar återfinns det kända resultatet, ingen dubbel commit.
   Drift eller okänt utfall kräver avstämning; inget reset/cleanup av eget arbete.
6. Worker rapporterar den riktiga committen. F16, Integration-review/merge och
   Donevillkoren gäller oförändrade; brokerkvitto är inte taskapproval eller Done.

Capabilityn exponeras bara i registrerad Worker-komposition; befintliga standard-
kataloger och Integration/Coordinator får ingen ny commitbehörighet.

## Isolerade oberoende tester och faktisk gate

Betrodd Settings-testargv använder verifierad Bubblewrap-process med egen PID-/
nät-/mountvy, skrivskyddad targetarbetskatalog, privat scratch och minimala runtime-
filer. Skyddad hoststate, credentials, sockets och andra worktrees monteras inte.
Verifiera faktisk process/exit/commit/kommandohash i varje befintlig F16/F21/F36/F08-
kontroll. Inget påstående att minimal miljö ensam är isolering.

RuntimeGateEvidence måste härledas från verkliga ofarliga negativa prov och
registrerat aktuellt start-/broker-/testkontrakt. Bind hela kompositionens hash
till rootkonfiguration och faktisk main. Kontrollera extern läsning, skyddad state,
Git-/rollgräns, isolerade tester och faktisk input-readiness. Ny main/config kräver
färsk avstämning. Ingen literal True-only-verifierare eller återanvändning av F40:s
begränsade permit accepteras som generell grind. Innan detta passerar: WAIT.

## Verkligt scenario och tidslinje

- Separat ofarligt repo, egen named Herdr-server och verkliga User-tilldelade
  TeamPlayer-testepics/tasks. Skyddad operatörskonfiguration skapas före effekter.
- Verklig registrerad preflight kan mäta Worker-gränser före första autonoma claim;
  det är ingen adoption av utvecklingsworktree och ger inga fabricerade slots/SID.
- Primär epic har tre tasks: två oberoende kandidater för två samtidiga Workers
  och en beroende task. Endast en epic får vara aktiv; unika taskägare/slots krävs.
- En ärligt dokumenterad kontrollerad första kodbrist får användas för att verifiera
  taskkorrigering. Worker redovisar verkliga baselineprov och begränsning; native
  Integration måste granska det faktiska felet och återföra konkreta inom-scope-
  krav till samma Worker. Inga fabricerade fel, testresultat eller godkännanden.
- En verklig aggregatebrist leder till F39-korrigering genom registrerad template
  och samma Integration. Bevara tidigare Done-tasks och kräv ny aktuell full review.
- Native Coordinator väljer/claimar, granskar och levererar genom riktiga services;
  ingen manuell scheduling, taskmerge eller epicmerge. Efter ny main och full Done-
  synk ska den lilla beroende epicen starta från just den nya main-committen.
- Tidslinjen binds till faktiska sessioner, operationer, SHA, verifiering och board-
  återläsningar. Mät två samtidiga reserverade/aktiva Workers, aldrig tre. Stoppa
  egna processer och bevara deras worktrees/refs/historik efter verifieringen.

## Verifieringsgrind

Riktade regressioner för gamla och nya startkontrakt, profil-/roll-/PID-/scope-
nekanden, commit/retry/drift och faktisk filsystem-/testisolering. Ny nativekod
fryses före verklig körning. All acceptans står kvar omarkerad tills det verkliga
scenariot, aktuell review, Task→E10 och relevanta eftertester är genomförda.


## Nativeupptäckt: automatisk aktivering efter registrerad ACK

Den andra dataseten verifierade exakt native Integration-ACK/registrering och
aktuell OS-/brokergrind, men modellen avslutade sin starttur efter ACK. En prompt
som säger att fortsätta är inte leveransbevis. Operatören aktiverade därför denna
befintliga session en gång för **diagnostik**, utan taskval eller merge. Full
F41.A1 räknas inte som uppfylld i den dataseten. Båda Workers har därefter faktiskt
observerats arbeta med unika slots/SID; taskkorrigering och resten av kedjan
provas för att hitta konkreta återstående fel före en ny samlad autonom körning.

Före nästa kodändring fastställs det minsta kompletterande stödet: ett valfritt,
betrott och konfigurationsbundet post-ACK-aktiveringssteg i befintlig epicstart.
Standard utan aktivering bevarar tidigare enbart-ACK-beteende och fingerprint.
F41-kompositionen väljer aktivering uttryckligen; agentargument kan inte slå på
den eller skicka fri text/kommandon. Ingen generell send-agent-förmåga tillkommer.

Steget får bara köras efter verklig exakt F34-registrering i ursprunglig native
Integration/SID/cwd/processbindning, inom Coordinatorns faktiska F38-reservation.
Spara en unik epicbunden operation före eventuell prompt: ursprunglig start,
konfiguration/spec-/meddelandehash, SID/generation och observerade baseline-turns.
Den fasta kontrollinstruktionen ber Integration genomföra redan registrerat scope
med dess befintliga tjänster; den väljer inga tasks, granskningsbeslut eller merge.

Bekräfta faktisk korrelerad native meddelandeleverans i samma session. Transport-
retur eller agentpåstående räcker inte. Vid tappat/okänt svar observeras samma
intention; skicka aldrig en andra prompt blint. Fel roll/scope/ändrad start eller
konfiguration nekas före effekt. Ett aktiveringskvitto är varken taskframgång,
review, merge eller Done. Tidigare ACK, deadline och försöksdata får inte ändras
retroaktivt. Spara faktisk diagnostisk operatörsinteraktion och interruption också.

Verifiera defaultkompatibilitet, faktisk registrering före effekt, replays,
förlorat svar före/efter leverans och drift. Frys sedan den nya kompositionen och
kör en ny samlad riktig tvåepics-körning utan manuell Integration-aktivering,
scheduling eller merge. Tidigare diagnosbevis redovisas separat.
