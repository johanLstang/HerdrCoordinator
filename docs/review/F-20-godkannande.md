# F-20: godkännande av aktuellt granskat underlag

## Beslut och källor

MCP `task_approve` kräver en skyddad operatörsregistrerad Integration-principal inom rätt projekt och epic. Operatörens `review_context` och `worker_test_command` aktiverar verktyget; en Herdr-session behövs inte för denna operation och startas inte implicit.

Input är `project_id`, `task_run_id`, stabil `request_key` och `decision`:

```json
{
  "version": 1,
  "result": "APPROVED",
  "context_id": "<64 tecken från aktuell F-18-kontext>",
  "summary": "Vad granskaren faktiskt har kontrollerat",
  "verified_criteria": ["Varje exakt kriterium i specifikationens ordning"]
}
```

Beslutet begränsas till 64 KiB och kräver meningsfull text. Tom, ofullständig, duplicerad eller främmande kriterielista avvisas. Input kan inte ange granskare, roll, testresultat, SHA eller ersätta reviewmaterial. Identiteten kommer från anslutningen och sparas av servicen.

F-18:s gemensamma `current_context` kontrollerar sparad contextidentitet/digest, immutable F-15-spec, verklig F-16-handoff, full aktuell Git-diff/rent worktree, operator-konfiguration och SUCCEEDED-testoperation för exakt versionspar. Paketet måste vara det senast begärda underlaget för tasken. En senare misslyckad eller oavstämd contextrequest gör ett äldre paket oanvändbart även när kod-SHA inte ändrats. Negativt och positivt beslut använder samma kontroll.

## Registrering och användbarhet

Parentintent innehåller autentiserad granskare, beslut, context-ID, task/epic-SHA och verklig testreferens. F-07 registrerar Review och REVIEWING → APPROVED under sitt repo-lås och SQLite-transaktion. Parentcheckpoint blir SUCCEEDED först efter återkontroll av aktuellt paket, senaste Review, fullständig acceptans och samma approvalpins. Inga caller-booleans används som Git- eller testbevis.

`require_current(actor, task_run_id)` återkontrollerar att det finns en slutförd F-20-parentoperation, rätt granskarscope/roll, senaste positiva Review och samma context/test/task/epic-pins. Det är ett verifierat läsresultat vid anropet. F-21:s leveransoperation måste dessutom kontrollera sitt aktuella underlag och Git-mål före den egna mutationen; en tidigare lyckad kontroll är inget permanent lås.

APPROVED förblir Active och behåller slot/session/worktree. Godkännandet utför ingen merge, Done, stopp eller cleanup. Ändrad HEAD, bas, konfiguration, rapport eller ersatt paket avvisar användning av historisk approval. Review och sparade beslut raderas inte; intern status och äldre pins är historik tills ansvarig integration avstämt nästa steg. F-07:s faktiska synkoperation ogiltigförklarar äldre approval enligt dess kontrakt.

## Avbrott och beslut som konkurrerar

- INTENT före Review kan återupptas med samma nyckel efter aktuell kontroll.
- Review/APPROVED sparad före processförlust återläses genom F-07:s samma review-ID; parentcheckpoint avslutas utan extra review.
- En parent som fortfarande är PENDING räcker inte för `require_current`, även om den interna APPROVED-övergången redan har skett.
- Ändrat underlag efter delvis approval hindrar checkpoint/användning. Den kända historiska reviewn bevaras för avstämning.
- Identiska samtidiga anrop delar parent och Review. Ändrat beslut eller annan granskaridentitet under samma nyckel avvisas.
- Pending approval respektive changes requested hindrar ett konkurrerande nytt beslut och en ny reviewcontext. Ett faktiskt race får en vinnare, ingen dubbel eller motsägande statusframflyttning.

Saknat/korrupt underlag och okända fel svaras med avgränsade koder utan repository-/subprocess-output. Ny approval efter verklig kod-/basändring kräver ny giltig handoff/context och reviewfas enligt integrationsprocessen; en statusändring ersätter inte detta.

## Verifiering

Proven använder verkliga temporära Git-repositories, SQLite och testprocesser; Herdr/Codex-transport är kontrollerad. De täcker godkännande, upprepning/återöppning, nya commits på båda sidor, konfigurationsbyte, saknat test/handoff, korrupt/ersatt context, fel roll/run, ofullständig acceptans, avbrott efter review och positiva/negativa dubblettraces. Riktig MCP-klient/server kontrollerar autentiserad roll, scope och strikt input. Detta är ingen ny fysisk approval-/mergekörning; samlat native flöde till Done provas i F-22. F-17:s runtimegate inför autonom F-38 kvarstår.
