# F40-r1: granskningsunderlag för separat mainbeslut

**Historiskt beslutunderlag:** godkänt av användaren2026-10-20. Leveransen är nu
verifierad i [slutkvittona](F-40-native-final.json). Nedan bevaras exakt det underlag
som presenterades före tillstånd och merge.

## Det konkreta beslutet

Tillåt en begränsad leverans av den redan granskade första provepicen till main i
`/tmp/herdr-f40-r1-fixture-20261015/repo`. Tjänsten ska utföra exakt en no-ff-merge,
stoppa den ursprungliga Integration-runtime, köra registrerad oberoende
slutverifiering och först därefter synka provepicen Done. Nästa claim ska fortfarande
stoppas av den saknade generella D02/F17-runtimegrinden. Resurser och historik bevaras.

Det tidigare användarsvaret ”Ja, godkänn F40-r1” avsåg mapp och egna sessionsverktyg.
Backloggens fastställda F40-kontrakt punkt9/D02/F17 kräver separat beslut om denna
aktuella review/source/main-bindning. Ingen mainauktorisation är ännu skapad och
ingen mainmerge utförd. Utvecklingsprojektets main och E10 berörs inte av provmergen.

## Aktuell verifierad bindning

| Underlag | Faktiskt värde |
| --- | --- |
| Review | `39246d45-5cc2-4ee2-9bfb-3f1321ce4982` |
| Context | `db95e103a57ab5873298eafaf3e6b9d2e62144513abb270d14268fb0122b2b9f` |
| Epic/source | `6033883834da04f296d29beeaa9fca960fdbfc8a` |
| Nuvarande main/target | `b4919eb13d1934ec5c7ecafd98c5d748f58a0d18` |
| Root-run | `95b20746-e85a-4028-8e2e-f642a486beab` |
| Root-SID | `01a143de-70c8-71d3-9f24-11c5278e7517` |
| Integration-run | `6ab6cad1-9429-4d8c-81a0-5f98b903acda` |

Coordinatorns riktiga beslut är **EPIC_APPROVED**, återvaliderat som aktuellt.
Alla tre chunks av hela dokumentet (17 595 byte) har verkliga läskvitton.
Worker-commit `82b6b2023d78d41bd47485099a75e20707b8fa11` är granskad och integrerad
som source ovan. Oberoende task-/efter-/epicverifiering passerade; Worker är fysiskt
STOPPED och slotten frigjord. Instruktionsfilerna är bevarade. Andra provepicen
har inte startat. [Faktiska kvitton, kriterier, tester och diff](F-40-r1-main-review.json).

## Hela main–epic-diffen

```diff
diff --git a/f40_greeting.txt b/f40_greeting.txt
new file mode 100644
index 0000000..decfc9d
--- /dev/null
+++ b/f40_greeting.txt
@@ -0,0 +1 @@
+Hej Ada
```

## Kvar efter godkännande

Operatören återvaliderar samma bindning och registrerar ett skyddat engångstillstånd
utanför MCP. Samma native Coordinator begär därefter leverans genom F40-tjänsten.
Ändrad source/main/review kräver nytt aktuellt underlag, ingen automatisk merge.
Utvecklings-F40 kan först därefter slutgranskas, integreras till E10 och eftertestas
innan dess egen task får Done. E10 förblir Active tills hela epicen integrerats.
