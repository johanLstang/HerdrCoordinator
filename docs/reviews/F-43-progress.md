# F43: leveranskandidat med verifierat nativeprov

**Bedömning:** READY_FOR_REVIEW. Nativeacceptans3/3 med redovisad källkvalificering. Ingen leveransapproval, taskmerge eller Done ännu.

Slutlig Worker-kod `d162a8bc3f14526b498a6b37f363151705dd515b` på `task/e11-f43`, från E11 `a07dfc64a7e44b5ad36ab8e0d4cbaddb7bb6b15a`. Bootstrap Integration har gått igenom kontrakt, roll-/ownerbindning, slot-/resumejournaler, TeamPlayer-överlagring och faktiskt testunderlag. Full leveransreview återstår efter nativeprovet.

## Historisk verifiering före slutligt nativefynd

- Slutliga recoveryprov:20 PASS104.75s/exit0, faktisk Git/SQLite med kontrollerade Herdr/Codex/boardpeers.
- Regression:119 PASS584.17s/exit0. Gruppen föregår sista hold-grinden och uppdelningen av planverifiering.
- Kompletterande kontroll-/projektprov:7 PASS357.73s/exit0, inklusive stoppad registrerad korrigeringsplan, MCP-styrd review/merge/refill, park/input samt fyra tvåepics-/korrigerings-/lost-reply-fall.
- 143 unika fall; överlapp räknas en gång. Runtime-status/cursor verifieras i slutliga20.
- Ruff, build, READMECLI, wheel81 moduler+sex policies, 91 frysta källpins, credentialsökning och223 lokala dokumentlänkar PASS. Schema3/uv.lock/policies oförändrade.

Råa JUnit och källhashar: [verifieringsunderlag](../recovery/F-43-verification.json), [20 recoveryprov](../recovery/F-43-unit.xml), [119 regressionsprov](../recovery/F-43-regression.xml), [7 kontrollprov](../recovery/F-43-control.xml).

## Åtgärdade kodfynd

1. Teknisk Attention får inte låta befintlig scheduler-advance eller direkta taskskrivningar fortsätta genom kontrollgränsen.
2. Återhämtning får häva endast sin egen bekräftade TeamPlayer-överlagring, med bevarat historikblock och samma externa version; senare manuellt beslut lämnas orört.
3. En stoppad Integration med registrerad korrigeringsplan kräver ren medlemskapsverifiering utan liveförvillkor. Samma session återupptas sedan via F13.
4. Beständig övervakning innehåller både aktuell fysisk runtime-status och native turncursor. Varje poll verifierar originalresurserna på nytt.

## Historiskt hinder före användarens godkännande och R2

Den egna servern `hc-f43-r1-20261020` och en verklig STARTING-Coordinator finns. Samma run `1114f161-54c9-453d-8efa-0f02460e7314`, originaloperation `cf06e48d-ecba-4304-af87-a1744ba265ae`/AGENT_PENDING, agent `hc-c-f43f4267ddf84867aeaa920f` i `w1:p1`. Codex frågar om uppdatering0.162.1→0.163.0; användaren har tillfrågats om **Skip**. Herdr-skillen kräver beslut före svar på blockerad dialog. Ingen uppdatering, prompt, ACK, native sessions-ID, EpicRun, TaskRun eller slot har skapats efter detta stopp.

Originalsnapshot och slutlig separat recovery-snapshot är bevarade; konfigurationshash/ursprunglig MCP-hostpath är exakt oförändrade och snapshotkontrollen gjorde noll SQLite-skrivningar. Ingen run eller resurs adopteras. [Kontrakt, komposition och provresurser](../recovery/F-43-sessions.md).

Nästa ansvariga roll är operatör/bootstrap Integration: efter godkänt UI-val verifieras aktuell UI och samma ursprungliga start fortsätter genom produktservicen. Kör nativeprovet färdigt, granska exakt slutlig task-/epic-SHA, integrera no-ff Task→Epic och eftertesta innan Done. F44 får inte startas genom att kringgå detta beroende. E11 förblir Active och egenacceptans0/3.

TeamPlayer: F43 **NeedsApproval5/User105**, status och beskrivning synkade/återlästa. E11 **Active11**, beskrivning synkad/återläst, egenacceptans0/3. Backloggens feature och Kanbanrad är Attention. Ingen Git-merge har utförts.

## Uppdaterad leverans 2026-10-29

R1 kunde inte fortsätta eftersom servern försvunnit före prompt/ACK/SID; originalresurserna är arkiverade och adopteras inte. Användaren godkände Skip och förtroende för det separata R2-provrepoet och dess worktrees. Verkliga R2-fall A1/A2/A3 har passerat; [nativeunderlag](../recovery/F-43-native.json) redovisar alla sessioner, processer, originalnycklar, faktisk boardstatus och fysisk slutstoppning. Inga nativeverktyg eller kodändringar utfördes av provagenterna.

Nativeprovet hittade en blockerande kodbugg vid okänt resume-resultat efter faktisk slotreservation. Rättningen journalför färskt, på nytt ägarvaliderat subjekt i stället för gamla slotdata. Ny regression samt slutliga21 PASS101.30s verifierar detta. A2/A3 använder rättad frozen-recovery-v2; A1:s oförändrade positiva AST-väg och tidigare snapshot kvalificeras uttryckligen. Ett extra A1-försök är ogiltigförklarat och ger inga passerade kriterier; två kvarvarande tekniska journaler på stoppade provresurser lämnas explicit till F44/operatör, utan runtimeåterstart eller falskt Done.

Ny full granskning av exakt slutlig task-/epic-SHA, faktisk Task→Epic och eftertester återstår före Done. De äldre avsnitten ovan är historik från vänteläget.

Slutlig källcommit: `d162a8bc3f14526b498a6b37f363151705dd515b`. Aktuella21 prov, nativekvalificering och strukturella kontroller finns i verifieringsunderlaget. Senast återläst TeamPlayer Testing9/User105; lokal Active. Äldre väntetexter ovan beskriver inte aktuellt läge.
