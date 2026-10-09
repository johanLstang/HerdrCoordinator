# F29: beständig scheduling och seriell taskintegration

`TaskSchedulerService.tick(actor, epic_run_id)` genomför ett begränsat servicevarv
för en registrerad Integration-principal. Två Workers kan arbeta samtidigt;
rapporter, review och leverans drivs seriellt mot den aktuella epicen. Ledig säker
kapacitet fylls från ett nytt [F27-urval](F-27-korbarhet.md), även direkt efter en
verifierad taskleverans. C med beroende på A får börja först efter A:s verkliga
review, merge, eftertest, stopp, Done-händelse och TeamPlayer-Done.

## Betrodd komposition

Operatören konstruerar F24-läsaren, `TaskSelectionService` och
`TeamPlayerSyncService` med samma Settings, StateStore, project och verifierade
TeamPlayer-användare. Fullständiga specs, ordning, UUID-bindningar, tidigare
epicbevis, scope och externa förutsättningar följer F27. En tidigare registrerad
Coordinator måste ha skapat, startat och bundit produkt-epicen via F05/F03/F25.
Integration förhöjs aldrig till Coordinator. Endast en påbörjad epic tillåts.

```python
scheduler = TaskSchedulerService(
    settings, store, selection, sync, herdr, codex,
    processes=process_observer,
    review_provider=None,
    timeout_seconds=45,
)
result = await scheduler.tick(integration_actor, epic_run_id)
```

`settings.worker_test_command` och `review_context` måste vara kompletta.
Native adaptrar ska vara bundna till en uttrycklig Herdr-server och dess sandbox.
En beständig konfigurationshash binder start-/runtimeparametrar, specs, ordning,
bindings och tidigare epicunderlag. En annan principal eller ändrad konfiguration
kan inte överta journalen. Credentials sparas inte där.

Ingen CLI-komposition eller långlivad agent startas implicit. MCP aktiveras genom
`RuntimeService(..., task_scheduler=scheduler)` och `create_server`:

```json
{"project_id":"registrerat-lokalt-project", "epic_run_id":"registrerad-produkt-run"}
```

`task_schedule` är muterande och tillgängligt endast för anslutningens betrodda
Integration-principal. Extra roll-, spec-, approval-, status-, slot- och
retryargument avvisas. Worker och Coordinator nekas före nätanrop.

## Claim före sidoeffekter

Varvet autentiserar TeamPlayer och läser en ny fullständig board. F15:s interna
claimguard återvaliderar F27:s kompletta graf och verkliga leveransbevis under
samma Gitlås/SQLite-transaktion som unik run och [slotreservation](F-28-slots.md).
Native taskversion, User-tilldelning, specdigest, main-/epic-SHA och beroendebevis
sparas före Git eller Herdr.

En ny boardläsning kontrollerar tilldelning och kontrakt innan F05 skapar rätt
task-worktree. F25 binder den faktisk ägda resursen och synkar Planned med färsk
CAS-version. En ytterligare läsning och claimguard sker före F11/F12-start/ACK.
Native Active följer verifierad WORKING-ACK. TeamPlayer/Git/SQLite är separata
system; detta är ingen global atomisk transaktion. Manuella externa ändringar
kan inträffa mellan kontroller och måste stämmas av.

Oregistrerade eller tidigare manuella runs adopteras inte. Misslyckad claim med
faktiskt verifierat fel före varje runtimeintent får frigöra sin aldrig startade
slot. Försöket bevaras och scheduler börjar inte om med en ersättningsrun.

## Parallella rapporter och aktuell review

F16 verifierar native rapportens oförändrade commit mot Worker-källan: faktisk
ren branch/path/source samt F05-taskbas, eller senaste verifierade F07-synks
inkommande epiccommit. Ett oberoende test märks `purpose=worker_report` och
`source_base_commit`. Det saknar review-target och får inte användas som approval.

När A integrerats kan B fortfarande lämna sin originalrapport. F18 synkar sedan B
mot aktuell epic, testar det nya exakta task-/epic-paret och bygger full review.
Ingen rapportcommit skrivs om. Integration måste lämna ett faktiskt beslut.

Standard `review_provider=None` stannar i `AWAITING_REVIEW`. Befintliga F20/F19-
verktyg kan ge beslut. Alternativt får operatören registrera en betrodd funktion
som tar en kopia av den fullständiga aktuella kontexten och returnerar en
version-1 ApprovalDecision/ChangesDecision **som dictionary**, eller `None`.
En async funktion stöds. Beslutet ska bindas till aktuellt context-ID och verkligt
granskade kriterier; scheduler skapar inget godkännande. Testernas fasta reviewer
är en simulerad beslutsadapter, inte en produktpolicy för automatisk approval.

Ändras bara epicbasen efter en bevisad approval kontrollerar F20 dess fulla
historiska review/test/konfiguration/handoff samt oförändrad faktisk taskkälla.
APPROVED återköas till READY_FOR_REVIEW, approval-SHA rensas och historik,
session, worktree och slot bevaras. Ny full F18-verifiering och ett nytt beslut
krävs. Ändrad taskkod ger paus, inte sådan återköning.

Projektets schedulerlås avvisar överlappande ticks med `SCHEDULER_BUSY`. F21:s
separata gemensamma Gitlås och beständiga deliveryintent skyddar även mot andra
integrationsanrop. Faktisk Task → Epic-merge följer aktuell F20-approval, `--no-ff`,
eftertest och F13:s bekräftade stopp innan Done/slotrelease. Episka mainleveransen
är Coordinatorns separata ansvar.

## Journal, omstart och nästa åtgärd

Schema 2 behålls. `epic_schedule` binder epicens konfiguration/principal;
`task_schedule` binder varje pipeline till dess task-run. F15/F16/F18/F19/F20/F21/F25
behåller sina egna operationer och idempotensnycklar. Reviewnyckeln binds till
handoff och aktuell epiccommit; beslut sparas före dispatch och deliverynyckeln
binds till faktiskt approval-ID.

| Fas | Nästa åtgärd |
| --- | --- |
| CLAIMED / OBSERVING | Nytt varv fortsätter kända resurser och observerar samma runtime. |
| AWAITING_REVIEW | Integration granskar aktuell context/request_key och lämnar beslut. |
| MERGING | F21 fortsätter samma deliveryintent; inget nytt mergeförsök gissas. |
| SYNC_PENDING | F25 stämmer av/synkar med sparad orsak; redan verifierad leverans kör ingen Git, test eller runtime igen. |
| WAITING_INPUT | Operatör/Integration hanterar dokumenterad blockerare. F31/F32 inför automatisk park/input/resume. |
| PAUSED | Stäm av journalens konkreta reason, kända commits och runtime; ingen implicit retry/resume. F47 tillför recoverypolicy. |
| DONE | Leverans och spegling klara; senare varv återanvänder bevisen. |

Svaret visar started-runs och task-run-ID, fas, säker reason, aktuellt context-ID,
review-request_key, delivery_key och mergecommit. `ok=true` betyder att varvet
kunde genomföras; en enskild pipeline kan samtidigt vara pausad. Ingen falsk
Task-Done eller ny Worker skapas för att dölja ett fel.

Ett unknown start/test/stopp eller ändrad kod pausar endast berört steg. Slots
med osäker aktivitet behålls. Ett stopp kan ha följt en faktisk merge: granska
då sparad F21-merge/test/stoppjournal och slutför endast det saknade steget.
Vid förlorat TeamPlayer-svar läses utfallet tillbaka före ny skrivning.

## Verifiering och gränser

`tests/test_task_scheduler.py` använder riktiga temporära Git-worktrees, SQLite,
serviceflöden och SDK-MCP. Herdr/Codex/processer, boardtransport och beslutsadapter
är kontrollerade. Proven omfattar A/B-resultat samtidigt, C efter A, aktuell
B-review efter A, upprepning/återöppning, lås/identitet, approvalkö och ändrad
approvalkälla, testfel, osäkert stopp och synkfel efter lokal leverans.

Verklig native parallellitet och TeamPlayer-tidslinje provas separat i F30.
Varvet använder tjänsternas synkrona Git/runtime/testgränssnitt och StateStores
anslutning i sin egen tråd. Anropa inte samma SQLite-anslutning från hjälptrådar.
Långlivad Integration-agent kommer i E09; autonom Coordinator och skyddad
filesystem-/process-/testmiljö kräver fortfarande [F17:s/F38:s miljögate](../worker/F-17-verklig-worker.md).
Manuella utvecklingsbootstrap-worktrees blir aldrig produkt-runs.
