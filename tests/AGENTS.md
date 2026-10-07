# Instruktioner för verifiering

Läs [rotens AGENTS.md](../AGENTS.md) och den valda taskens acceptans i [Backlog.md](../Backlog.md). Dessa regler gäller tests och testfixtures.

- Knyt kontroller till konkreta acceptanskriterier och verkliga risker. Testa observerbart beteende och oberoende förväntade resultat.
- Använd temporära Git-repositories/worktrees och SQLite-filer. Prov mot Herdr/Codex och TeamPlayer använder avgränsade testepics och separata test-ID:n.
- Prova relevanta negativa fall: fel roll/projekt/task, smutsigt worktree, ändrad HEAD, saknat beroende, dubbel start och stale review.
- Prova avbrott efter varje berörd sidoeffekt. Verifiera att retry/recovery återanvänder kända resurser och inte fabricerar Done.
- Vid samtidighetsändringar: prova race mellan start/resume, unik taskägare, max två aktiva Workers och serialiserad integration. En simulerad adapter ersätter inte ett verkligt runtimeprov.
- Vid Attention: verifiera parkbekräftelse före slotrelease, bevarad session/branch/worktree och samma-session-resume först när kapacitet finns.
- Verifiera epicstatus separat: ej startad Planned, påbörjad Active även vid task-Attention eller alla tasks Done före main-integration, och Done först efter samlad review/merge/slutverifiering. Prova färsk epicversion, versionskonflikt, återläsning och retry av enbart misslyckad TeamPlayer-synk.
- Redovisa kommando, exitkod, miljö, relevanta commit-SHA och vilket acceptanskriterium provet styrker. Dokumentera externa prov som inte kunnat genomföras som kvarvarande arbete.
- Kör relevanta kontroller och föreskrivna grindar. Bredare omkörning behövs vid förändrat underlag, fel eller kvarvarande konkret risk.

F-01 fastställer pytest och kommandona i README. Instruktionsfilen gör inga test- eller implementationskriterier uppfyllda; redovisa faktiska testresultat för varje leverans.
