# Instruktioner för verifiering

Läs [rotens AGENTS.md](../AGENTS.md) och den valda taskens acceptans i [Backlog.md](../Backlog.md). Dessa regler gäller tests och testfixtures.

- Knyt kontroller till konkreta acceptanskriterier och verkliga risker. Testa observerbart beteende och oberoende förväntade resultat.
- Använd temporära Git-repositories/worktrees och SQLite-filer. Prov mot Herdr/Codex och TeamPlayer använder avgränsade testepics och separata test-ID:n.
- Prova relevanta negativa fall: fel roll/projekt/task, smutsigt worktree, ändrad HEAD, saknat beroende, dubbel start och stale review.
- Prova avbrott efter varje berörd sidoeffekt. Verifiera att retry/recovery återanvänder kända resurser och inte fabricerar Done.
- Vid samtidighetsändringar: prova race mellan start/resume, unik taskägare, max två aktiva Workers och serialiserad integration. En simulerad adapter ersätter inte ett verkligt runtimeprov.
- Vid Attention: verifiera parkbekräftelse före slotrelease, bevarad session/branch/worktree och samma-session-resume först när kapacitet finns.
- Redovisa kommando, exitkod, miljö, relevanta commit-SHA och vilket acceptanskriterium provet styrker. Dokumentera externa prov som inte kunnat genomföras som kvarvarande arbete.
- Kör relevanta kontroller och föreskrivna grindar. Bredare omkörning behövs vid förändrat underlag, fel eller kvarvarande konkret risk.

Tests är ännu inte en implementerad testsvit. F-01 fastställer ramverk och kommandon; instruktionsfilen gör inga test- eller implementationskriterier uppfyllda.
