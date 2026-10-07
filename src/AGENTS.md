# Instruktioner för implementation

Läs [rotens AGENTS.md](../AGENTS.md) och [Utvecklingsprocess.md](../Utvecklingsprocess.md). Dessa kompletterande regler gäller implementation under src.

- Implementera den plockade featuren i dess task-worktree. Håll domän, applikationstjänster, adaptrar, persistens och MCP åtskilda enligt arkitekturplanen; fastställ endast struktur som aktuell task behöver.
- Låt orchestratorn validera roller, state och kritiska operationer. MCP kopplar till services; adaptrar kapslar verifierade Git-, Herdr/Codex- och TeamPlayer-gränssnitt.
- Bind behörighet till betrodd anslutning/run. En rollsträng i agentens argument eller prompt ger ingen behörighet.
- Bevara project/epic/task/run, branch/worktree, session-ID och commits. Dokumentera unika nycklar, transaktioner och nullable-fält när persistens ändras.
- Spara kända delresultat vid partiella operationer. Upprepad start, merge, synk, resume och cleanup får inte skapa dubbla sidoeffekter. Okänt externt resultat kräver avstämning.
- Reservera Worker-kapacitet före start/resume. Frigör först efter bekräftad inaktivitet. Testa produktens två Workers utan att parallellisera arbetet med backloggens implementationsfeatures.
- Kontrollera branch, worktree, arbetsläge, roll och aktuella SHA före merge. Approval binds till exakt granskat underlag; ändrad bas kräver ny verifiering.
- Skilj Kanbanstatus från runtime och Gitbevis. TeamPlayers board epic saknar leveransstatus; använd dokumenterad epicstate och den verkliga adapterförmågan.
- Håll credentials utanför kod, loggar och agentkontext. Använd argumentbaserade processanrop och validerade paths.
- Vid ändrat schema eller kontrakt: uppdatera källan, taskens beskrivning, acceptans och relevanta recovery/driftinstruktioner. F-01 fastställer Python-, start- och testkonfigurationen.

Flagga vid review: saknade förvillkor, icke beständiga delresultat, otillåten merge-riktning, stale approval, osäker slotrelease och cleanup som kan förlora arbete.
