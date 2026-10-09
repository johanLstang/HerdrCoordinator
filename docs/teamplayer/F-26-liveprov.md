# F26: verifierat liveprov mot TeamPlayer

## Resultat

Verklig TeamPlayer-MCP, native Codex via Herdr och isolerat Git/SQLite provades
2026-10-09. Testtaskens status följde `Pending → InProgress → NeedsInput →
InProgress → Done`; testepicen följde `Pending → InProgress → Done`.
[Maskinföljbart underlag](F-26-prover.json) innehåller exakta ID:n och commits.
F26:s utvecklingstask blir Done först efter dess egen review, Task → Epic-merge
och integrationskontroller; testepicens Done ersätter inte den leveransgrinden.

## Avgränsade resurser

| Resurs | ID eller sökväg |
| --- | --- |
| TeamPlayer-projekt | `d2ee4c75-7b80-465f-83ac-1750854a8e80` |
| Verifierad utförare | `105f26a7-0648-438d-94fd-3260ac3af4ee` / blitterbot@gmail.com |
| Separat testepic | `f74e4cbb-e4e6-4644-9704-baba8e79bca3` |
| Separat testtask | `88460303-7eb4-4811-8f74-d5f5caa4dc12` |
| Utvecklingstask F26 | `1626d7a9-387d-47cd-b20b-86cb2a9f0613` |
| Lokal run / session | `195d1ed1-052f-48e3-ac3b-34a2bbee2a54` / `01a11ff3-8f54-7b70-893e-8a5a30f2f5e6` |
| Egen Herdr-server / agent | `hc-f26-20261008` / `hc-91b22030c5914bcb9b0a061e` |
| Lokal provjournal | `.herdr/probes/f26-r2/` i F26:s task-worktree |
| Godkänd gemensam reporot | `.herdr/probes/f26/repo` i samma worktree |

## Verifierad kedja

1. F05 skapade verkligt ägda epic/task-worktrees från seed
   `423eaf84507719680b15461d07835541e25cd2f9`. F15/F11/F12 startade en Worker,
   skickade uppdraget en gång och verifierade native ACK och session.
2. Workern rapporterade strict `BLOCKED` för saknat hälsningsprefix och ändrade
   ingen kod. F16 kontrollerade rapport/proveniens. F13 bekräftade fysisk exit
   innan `PARKED` och slotfrisläppning. F25 skrev och återläste `NeedsInput`.
3. F13 återupptog samma session, branch och worktree med reserverad slot.
   F25 skrev `InProgress`. Testoperatören gav exakt prefix `Hej`; beständig
   inputavsikt och native historik bekräftade ett enda inputmeddelande.
4. Workern committade `079b6f25616c81c30b59ba685087d8a821cade40`, inom
   README/greeting/test-scope. Nio unittesttester täcker prefix, whitespace,
   Unicode, kombineringstecken, typer och tomma namn. Oberoende verifierare
   utanför Workers scope kontrollerade ytterligare exakta resultat och fel.
5. F16 verifierade leveransen. F18 skapade aktuell komplett reviewkontext
   `36e6093b33ae93a3c63db75d0f67596954071ce8cad8b37e85ddfac5a9ee891d`.
   Operatören granskade hela diffen (3451 bytes,
   SHA256 `cf3bd3328bbc43390e863eb4977c2fecfe0503f9dae5f2c88c855f326d0adccb`),
   källor, kriterier och oberoende test. F20 registrerade aktuellt godkännande.
6. F21/F07 gjorde faktisk no-ff-merge
   `7197a73b183bd3613968a8fac534c20e8bd56413`, testade just den och bekräftade
   fysisk stopp före Done. Replay gav `EXISTING` med samma merge; ingen extra
   Worker, resume eller taskmerge skapades.
7. Ett faktiskt stängt MCP-klienttransport avvisade Done-skrivningen. F25
   bevarade `PENDING/TEAMPLAYER_WRITE_OUTCOME_UNKNOWN`, operation
   `2f125089-7b54-4054-9869-ad4238e100e5`. Efter återanslutning slutfördes samma
   operation med läsning/CAS/historik. Fyra unika status-/historikevent finns
   exakt en gång; testtasken återlästes Done/version 9.
8. F08 verifierade testepicen, registrerade manuellt aktuellt epicbeslut,
   mergade no-ff till fixturens main
   `d4215a26ba5e77d64a1fd4aed4117e5313c43563` och sluttestade den.
   Testoperatören registrerade Done från verkliga merge-/testfakta. F25
   kontrollerade den kompletta kriterielistan och skrev epic-Done/version 4.

## Scope, nätfel och credentials

Harnessen [f26_native.py](../../scripts/probes/f26_native.py) använder levererade
produktservices. En skrivgräns tillåter bara testepicens status och testtaskens
status/beskrivning i det verifierade projektet. Skrivavsikter journalförs med
ID, version och status, utan headers, credentials eller beskrivningstext.
Tio faktiska skrivförsök gick endast till dessa två fixture-ID:n.

Full stabil före/efter-läsning genom F24 kontrollerade alla andra boardobjekt.
F26:s egen utvecklingstask dokumenteras separat: manuell utvecklingsprocess
ändrade dess status/version/beskrivning för trust-hindret och återupptagningen.
Harnessen skrev inte den tasken. Dess övriga domänfält och alla orelaterade
epics/tasks förblev oförändrade (digest i provfilen).

Detta provar ett verkligt stängt SDK-transport med återanslutning. F25:s
föregående nativeprov täcker också förlorat klientsvar efter lyckad skrivning;
simulerade adaptrar täcker övriga versions-/felgrenar. De fallen redovisas
separat och detta prov påstår inte att servern accepterade det stängda
transportets skrivning.

## Godkännande och avbrutet första försök

Användaren godkände 2026-10-09 `Trust and continue` för `.herdr/probes/f26/repo`.
Operatören inspekterade exakt dialog och valde detta godkännande. En frivillig
Daybreak-banner och ett erbjudande om Codex-uppdatering avfärdades; ingen
programuppdatering eller ytterligare behörighetsinställning infördes.

Första försöket `.herdr/probes/f26/` fastnade på trust före assignment/ACK/session.
Dess egen server var därefter stoppad. Omstart återställde en shell med nytt
terminal-ID; F11 avvisade korrekt gamla bindningen (`RUNTIME_PANE_CHANGED`).
Originalets databas, startoperation, gamla slotreservation och boardbaslinje
bevaras. Det är ett avbrutet startförsök, inte en lyckad recovery eller parkering.
Inga ägar-, terminal- eller sessionsbevis skrevs om för att kringgå kontrollen.

Ett nytt sekventiellt F05-prov med egen databas och nya lokala branch/run-ID:n
använde samma godkända reporot och samma externa testobjekt. Gammal runtime
var fysiskt borta; återställda pane hade endast sin shell och inga agenter.
Originalets Planned-baslinje behölls och testepicen rullades inte tillbaka.
Detta är manuell testoperatörshantering; F47:s recoverypolicy är framtida arbete.

## Körning och granskning

Kör från F26:s task-worktree:

```bash
uv run --locked python scripts/probes/f26_native.py <phase> --attempt 2
```

Förberedelse kräver en ny journal; kör inte om `prepare` mot bevarade prover.
Faserna är `bind-planned`, `activate`, `start`, `sync-active`,
`collect-blocked`, `park`, `sync-attention`, `resume`, `sync-resumed`,
`input` (skicka, sedan avstäm native historik), `collect-ready`, `review`,
`approve`, `deliver`, `sync-disconnected`, `sync-done`, `epic-verify`,
`epic-approve`, `epic-merge`, `epic-final`, `epic-done`, `sync-epic-done`, `export`.
Kontrollera varje resultat innan nästa beroende fas. `REPORT_WAIT` betyder
att slutrapport inte är verifierad; gör ingen review eller merge då.
Operatören granskar hela aktuella underlaget före godkännandefaserna.

Detta är ett avgränsat prov med en Worker och manuella input-/review-/slutbeslut.
Automatisk inputpolicy, långlivad Coordinator-loop och F38:s isolering för
säker autonom drift är senare leveranser. Fixtureworktrees, sessionshistorik
och databaser bevaras; ingen cleanup utförs.

## Utvecklingskontroller

`uv run --locked pytest tests/test_startup.py`: 20 passerade (9.37 s).
Ruff, build, config-CLI, diffkontroll och lokala dokumentlänkar passerade.
Saneringskontroll av harness, provrapport och detta dokument mot aktuella
privata header-/bearervärden passerade utan att skriva ut dessa värden.
Separat processkontroll av originalets cwd visade enbart återställd pane-shell.
Den kontrollen finns också som guard före framtida retryförberedelse; dess
observation läggs inte retroaktivt in i det ursprungliga startförsöket.

Ett för tidigt reviewförsök innan native Worker-idle/READY avvisades. Efter
slutrapport gjordes ny oberoende rapportkontroll och aktuell review. Inga
förvillkor eller tester försvagades för att få provet grönt.
