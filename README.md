# HerdrCoordinator

HerdrCoordinator ska automatisera utveckling av epics och tasks med Codex-agenter via Herdr, isolerade Git-worktrees och TeamPlayer Kanban. En deterministisk orchestrator ska validera och utföra kritiska operationer samt lagra runtime-information i SQLite. Projektet har ännu ingen implementation; dokumenten nedan beskriver mål och arbetsprocess.

## Projektdokumentation

- [Arbetsprocess](Herdr_Workflow.md) — agentroller, task-livscykel, review och merge.
- [Arkitektur och implementationsplan](Arkitektur%20och%20implementationsplan%20%E2%80%93%20Herdr%20-Codex%20Multi-Agent%20Workflow.md) — komponenter, runtime-kontrakt och implementationsfaser 1–12.
- [Guide för epics och features](Epic%26Feature%20Guide.md) — projektets mall för backlogg, körbara Worker-uppdrag och verifierbar acceptans.
- [Komplett backlogg](Backlog.md) — 12 epics och 50 tasks med beroenden, arbetsinstruktioner, acceptans och Kanbanöversikt.
- [Utvecklingsprocess](Utvecklingsprocess.md) — en feature i taget, plockning i TeamPlayer, taskintegration och epic-PR.
- [Agentinstruktioner](AGENTS.md) — gemensamma regler med kompletteringar i src, tests och prompts.

Använd guiden när nya epics och features planeras. TeamPlayer är primär källa för arbetsstatus när kopplingen är etablerad; dokumentöversikten speglar den. En task blir `Done` efter godkänd review, merge till epic-branchen och integrationstester. En epic blir `Done` efter slutreview, merge till `main` och slutverifiering.
