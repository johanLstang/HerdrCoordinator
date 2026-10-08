# F-19: konkret feedback till samma Worker

## Beslut och behörighet

Integration granskar F-18:s kompletta, aktuella underlag enligt den paketerade [version-1-policyn](../../prompts/integration-review-v1.md). `render_review_prompt(context)` återger policy och JSON för en explicit granskningssession; den startar ingen autonom Integration Agent och utfärdar ingen rollbehörighet. Runtime-principal och services upprättar behörigheten.

MCP `task_request_changes` tar `project_id`, `task_run_id`, stabil `request_key` och ett strukturerat beslut:

```json
{
  "version": 1,
  "result": "CHANGES_REQUESTED",
  "context_id": "<64 tecken från verifierad reviewkontext>",
  "issues": [
    {
      "number": 1,
      "problem": "Ett konkret problem i den granskade versionen",
      "requested_change": "Avgränsad korrigering och hur den verifieras",
      "acceptance_criteria": ["Exakt befintligt acceptanskriterium"]
    }
  ]
}
```

Beslutet tillåter 1–32 sammanhängande numrerade problem, meningsfull text och högst 16 KiB kanoniskt JSON. Refererade kriterier måste finnas i taskspecifikationen; nya scopekrav får inte smugglas in. Worker, främmande task/epic, stale context, tom feedback, extra fält och APPROVED genom detta negativa beslut avvisas. Käll-/testunderlag och konfiguration återkontrolleras mot current task/epic-SHA innan registrering. Ingen test- eller rollboolean tas från agentens argument.

## Beständig review och leverans

F-07 registrerar Review med nästa reviewnummer, exakt task/epic-SHA och strukturerad feedback, och flyttar REVIEWING → CHANGES_REQUESTED. Parentoperationen sparar context-ID, beslut, runtimebindning, session, branch/worktree och korrelerad korrektionsprompt. Ingen SQLite-schemaändring behövs.

Före första dispatch måste samma F-11-start/processbindning, slotreservation och native Codex-session vara verifierade och redo för input. Dispatchavsikt, deadline och tidigare turn-ID:n sparas före det enda promptanropet. Worker ska först skicka exakt ACK med projekt/epic/task/run, reviewnummer/-ID, context-ID och correlation-ID. Ny native agentMessage, exakt levererad userprompt och samma runtime/session kontrolleras innan CHANGES_REQUESTED → WORKING sparas atomiskt. Transportretur och äldre ACK räcker inte. F-17:s interrupted-metadatafall accepteras endast när fysisk runtime oberoende visar working.

Slot, session, branch och worktree bevaras genom review/fix. Den nya slutrapporten måste finnas efter just korrektions-ACK:n; F-16 kontrollerar både dess proveniens och verkliga nya Git-/testresultat. Därefter bygger F-18 ett nytt aktuellt paket. Äldre rapport eller context ger inget nytt approval. F-20/F-21 hanterar godkännande och leverans separat.

Rapportens `tests` gäller levererad commit. Historiska röda TDD-prov redovisas ärligt i `test_summary`/`summary`. Fel för den levererade commiten får inte döljas där: F-16 fortsätter avvisa varje rapporterat misslyckat test i testlistan och kräver oberoende aktuell verifiering. Ett förtydligande av en avvisad rapport sker explicit i samma verifierade redo session, med bevarad historik; inga äldre failures filtreras automatiskt bort.

## Recovery

| Känt läge | Återförsök |
| --- | --- |
| INTENT före Review | Registrera samma negativa beslut genom F-07; samma nyckel ger ingen extra review. |
| Review sparad men parentcheckpoint saknas | Återläs samma Review och gå vidare till REGISTERED. |
| REGISTERED före dispatch | Kontrollera aktuellt underlag, samma redo runtime och native baseline före en ny dispatchavsikt. |
| DISPATCH_REQUESTED | Observera bara; även avbrott före faktisk send har okänt utfall. Ingen blind resend. |
| ACK mottagen men processavbrott före persistens | Återläs samma korrelerade native signal; skapa inget nytt event eller prompt. |
| CONFIRMED | Historiskt resultat återläses med samma nyckel även efter nya Worker-commits. |
| TIMED_OUT | Bevara CHANGES_REQUESTED, historik och slot. Sen ACK återställer inte deadline eller state. Integration avstämmer nästa åtgärd. |

Ändrat beslut under samma nyckel avvisas. Samtidiga dubbletter delar operation, Review och dispatch; checkpoint kan inte backa från DISPATCH_REQUESTED till REGISTERED. Ett annat beslut får inte ta över en task med oavstämd korrigeringsoperation. Runtime-, sessions- eller konfigurationsavvikelse bevaras utan osäker cleanup/release.

MCP aktiveras bara med F-18:s operator-konfiguration och explicit Herdr-session. Ingen server/Worker startas av ett feedbackanrop. Externa beslut rapporteras som blockerare; automatisk Attention-parkering kopplas in i E-08. F-17:s runtimegränser inför autonom F-38 kvarstår.

## Verifiering

Serviceproven använder verkliga temporära Git-worktrees, SQLite och testprocesser med kontrollerad Herdr/Codex-transport. Negativa roller/beslut, versionsbyte, utebliven slot, främmande session/ACK, avbrott efter Review respektive dispatch, timeout, återöppning och dubblettrace provas. Riktig MCP-klient/server kontrollerar roll och strikt input. Det [separata native korrigeringsprovet](F-19-verklig-korrigering.md) visar faktisk Worker-fix; simuleringarna redovisas separat.
