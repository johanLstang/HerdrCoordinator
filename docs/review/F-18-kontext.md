# F-18: komplett och aktuell reviewkontext

## Operatörens konfiguration

Välj `worker_test_command` enligt målrepositoryts README och ange epicens regler i lokal TOML. IDs måste matcha den registrerade produktens projekt och epic, inte utvecklingsbranchens namn. Exempel:

```toml
worker_test_command = ["uv", "run", "--locked", "pytest"]
worker_test_timeout = 300

[review_context]
version = 1
project_id = "registered-project-id"
epic_id = "registered-epic-id"
requirements = ["Bevara scope och task-worktree-isolering"]
acceptance_criteria = ["Granska aktuell task mot aktuell epic"]
sources = ["README.md"]
```

MCP `task_review_request(project_id, task_run_id, request_key)` kräver en operatörsregistrerad Integration-principal för samma epic. Anropet kan inte ange roll, testkommando, commits eller ersätta epicregler. `request_key` är en stabil avsiktsnyckel, högst 128 tecken. Worker och främmande projekt/epic avvisas. Utan konfiguration registreras inte verktyget. Ingen runtime skapas implicit.

## Bevis och tillstånd

Tjänsten kräver F-15:s oförändrade sparade taskspecifikation och F-16:s oberoende verifierade READY_FOR_REVIEW-handoff. Senare Worker-commits kräver ny handoff; enbart Git-ancestry räcker inte. Endast verkliga, journalförda Epic → Task-synkcommits får förlänga den verifierade leveransen.

Sedan [F29](../scheduling/F-29-scheduler.md) verifieras parallella Worker-rapporter
mot sin egen registrerade källbas, även när epicen ändrats. Dessa tester märks
purpose=worker_report och saknar review-target. F18:s efterföljande synk och nya
test på exakt aktuellt task/epic-par krävs alltid före beslut och leverans.

F-07 synkroniserar mot aktuell epic och kör operatörens argv utan shell i task-worktreet. Testprocessens minimala miljö är samma som i F-16; detta är inte en ny filsystemssandbox. Källor läses som Git-blobbar vid respektive granskad commit. Absoluta, skyddade och icke kanoniska paths, symlinks, binärtext och saknade filer avvisas; inga externa dokument hämtas.

Det beständiga paketet innehåller taskspecifikation, acceptans, epicregler, källtext/blob-ID, ändrade filer, komplett diff, testoperation och exakta task/epic-SHA. `patch_base64` bevarar patchens bytes; `display_text` kan innehålla ersättningstecken och är en visning. Paketets `context_id` är SHA256 över kanoniskt JSON utan själva ID-fältet. Varje källa begränsas till 256 KiB, varje sida till 32 källor och hela paketet till 4 MiB. Diffen följer GitAdapters separata storleksgräns; trunkering är inte godkännbar.

Aktuella commits, rena worktrees, versionsparets passerade tester och komplett paket kontrolleras igen innan paketet och READY_FOR_REVIEW → REVIEWING sparas atomiskt. Tasken ligger kvar i Active, håller sin reservation och får inget granskningsbeslut, approval, leveransmerge eller Done. Dessa steg tillkommer i F-19–F-21.

## Avbrott och återförsök

Operationsjournalen sparar INTENT, SYNCED, TESTED och READY. F-07 har egna beständiga synk-/testavsikter. Ett avbrott efter känd testframgång återanvänder resultatet när samma nyckel återupptas. Två samtidiga identiska anrop delar operation och verifiering.

- Konflikt ger `REVIEW_SYNC_CONFLICT`. MERGE_HEAD, filer och slot bevaras; befintlig Blocked-state blir inte REVIEWING.
- Testfel ger `REVIEW_TEST_FAILED`; ofullständigt Git-underlag ger `REVIEW_GIT_INCOMPLETE`.
- En påbörjad testoperation med okänt utfall ger `REVIEW_TEST_OUTCOME_UNKNOWN`. Den körs inte automatiskt igen.
- Ändrat versionspar eller operatörskonfiguration gör det gamla paketet oanvändbart. Ingen äldre testframgång flyttar state framåt.
- Terminala misslyckanden återläses oförändrade med samma nyckel, även när konfliktworktreet fortfarande är smutsigt. Integration avstämmer orsaken och använder först därefter en explicit ny nyckel för ett nytt granskningsförsök.

Granskningsmaterial är repositoryinnehåll. Publicera det inte osanerat i loggar. Fel svaras med avgränsade koder, inte rå subprocess-output.

## Verifieringens omfattning

Serviceproven använder riktiga temporära Git-repositories, SQLite-transaktioner och testprocesser. Herdr/Codex-historik och transport är kontrollerade testadaptrar. Proven täcker ändrad bas, konflikt, förlorat testutfall, avbrott efter verifiering, dubblettrace, stale context, fel roll/scope och förbjudna källor. MCP-rollkontrollen provas genom riktig MCP-klient/server. Detta är inte ett nytt verkligt Worker/review/fix-prov; det genomförs i F-22. F-17:s dokumenterade runtimegränser inför autonom F-38 kvarstår.
