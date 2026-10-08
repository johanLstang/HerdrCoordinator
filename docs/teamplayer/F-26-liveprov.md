# F26: liveprov mot TeamPlayer

## Aktuellt läge

Provet är förberett men inte färdigverifierat. F26 har `NeedsApproval` i
TeamPlayer och `Attention` i backloggen. E06 är fortsatt `Active`; dess andra
acceptanskriterium och F26:s tre kriterier återstår.

Codex visar en faktisk `Folder access`-dialog för det nya ofarliga repot
`.worktrees/task-e06-f26/.herdr/probes/f26/repo`. Herdrs instruktioner kräver
användarbeslut innan operatören svarar på en sådan dialog. Tidigare godkännande
gällde F10:s andra reposökväg. En fråga om F26:s sökväg har skickats; svaret
har ännu inte kommit. Ingen trust-knapp har valts automatiskt.

## Avgränsade resurser

| Resurs | ID eller sökväg |
| --- | --- |
| TeamPlayer-projekt | `d2ee4c75-7b80-465f-83ac-1750854a8e80` |
| Verifierad utförare | `105f26a7-0648-438d-94fd-3260ac3af4ee` / blitterbot@gmail.com |
| Separat testepic | `f74e4cbb-e4e6-4644-9704-baba8e79bca3` |
| Separat testtask | `88460303-7eb4-4811-8f74-d5f5caa4dc12` |
| Utvecklingstask F26 | `1626d7a9-387d-47cd-b20b-86cb2a9f0613` |
| Lokal produktrun | `f5b1db25-7a1a-4baa-b69c-dad545c507b1` |
| Egen Herdr-server | `hc-f26-20261008` |
| Native agent / pane | `hc-4dc1c8486db84b23a587425a` / `w1:p1` |
| Isolerad databas och journaler | `.herdr/probes/f26/` i task-worktreet |

Den lokala runnen är `STARTING`, med slot 1 reserverad och en beständig
`RUNTIME_BLOCKED`-operation. Ingen assignment, ACK, Codex-session, implementation
eller merge har bekräftats. Testepicen är korrekt speglad till `InProgress`,
testtasken ligger kvar i `Pending`.

## Harness och avsedd verifiering

Kör från F26:s task-worktree med `uv run --locked python
scripts/probes/f26_native.py <phase>`. `prepare` kräver ett nytt provområde;
kör inte om det för detta befintliga prov.

Harnessen använder levererade produktservices och sparar deras verkliga
operationer. En skrivgräns tillåter endast testepicens status och testtaskens
status/beskrivning i det verifierade projektet. Skrivavsikter med ID, version
och status journalförs utan headers, credentials eller beskrivningstext.

Planerade steg är verklig start/ACK, avsiktligt saknad hälsningsprefix-input,
`BLOCKED`-rapport, fysisk parkering, `NeedsInput`, återupptagning av samma
session och en beständigt korrelerad input. Därefter följer oberoende tester,
granskning av aktuell diff, godkännande, faktisk taskintegration, tester och
fysisk stopp före slotfrisläppning och `Done`. Ett faktiskt stängt MCP-klient-
transport provas vid Done-skrivningen; återanslutning ska bara slutföra synken.
Testepicen granskas och integreras också till fixturens main före epic-Done.

Full före/efter-avstämning kontrollerar alla andra boardobjekt. F26:s
utvecklingstask dokumenteras separat: dess status, version och beskrivning
ändras av den manuella utvecklingsprocessen när detta verkliga hinder
rapporteras. Harnessen får aldrig skriva den tasken; dess övriga fält och
alla orelaterade boardobjekt ska förbli oförändrade.

## Återupptagning efter beslut

1. Inspektera samma namngivna agent och den aktuella dialogen. Vid godkännande
   för just F26-repot kan operatören välja `Trust and continue`.
2. Återläs utvecklingstaskens version och tilldelning. När hindret är löst,
   återställ `InProgress`/`Active` och dokumentera beslutet.
3. Kör `start` för att avstämma samma beständiga startoperation. Skapa inte
   ytterligare agent, run eller slot för att kringgå hindret.
4. Följ harnessens faser och granska verkliga underlag före `approve` och
   `epic-approve`. Exportera bevis först när samtliga kontroller passerar.

Privata journaler och fixtureworktrees bevaras vid väntan. Inga gamla prover
modifieras och ingen senare beroende backloggfeature startas.

## Utförd verifiering och begränsningar

`uv run --locked ruff check scripts/probes/f26_native.py` och `git diff
--check` passerar. `sync-epic-active` genomfördes mot verklig TeamPlayer och
bekräftade synk. F26:s fulla livscykel, nätåterhämtning och slutliga
före/efter-avstämning har **inte** verifierats ännu.

Detta är ett avgränsat prov med en Worker och manuella testoperatörssteg.
Automatisk inputpolicy, långlivad Coordinator-loop och F38:s isolering för
säker autonom drift är senare leveranser.
