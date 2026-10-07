# Instruktioner för agentprompts

Läs [rotens AGENTS.md](../AGENTS.md), [Herdr_Workflow.md](../Herdr_Workflow.md) och aktuell task i [Backlog.md](../Backlog.md). Dessa regler gäller prompt- och policytexter under prompts.

- Skapa prompttexter när relevant feature levereras. Håll Coordinator-, Integration- och Worker-policy separata och versionshanterade.
- Coordinator väljer epics och slutgranskar samt begär Epic → main-merge. Integration styr Workers, taskreview, Task → Epic-merge och epicöverlämning. Worker implementerar en task, testar, committar och rapporterar.
- Coordinator håller epicstatus Planned/Active/Done synkroniserad mellan TeamPlayer och backlogg. Påbörjad epic förblir Active under review, hinder och väntan på main-integration/slutverifiering; Done kräver hela leveransgrinden. Task-Attention ändrar inte epicen till Attention. Prompten ska kräva färsk epicversion och återläsning vid statusskrivning.
- Worker-prompt innehåller verifierade task/epic/run-ID:n, branch/worktree, bascommit, mål, scope, källor, acceptans, beroenden och externa förutsättningar.
- Prompten återger behörighet som runtime redan har upprättat. Den utfärdar inte rollbehörighet och ger inte instruktion att kringgå services, statusregler eller filesystemgränser.
- Kräv maskinläsbara rapporter enligt taskens verifierade schema. READY_FOR_REVIEW anger commit, tester, sammanfattning och begränsningar; BLOCKED anger orsak och behövd input. Använd TASK/TASK_ID-normalisering först när F-14 fastställt formatet.
- Korrigeringsfeedback och svar på Attention går till samma Worker-session med korrekt task/run. Ange konkret kriterium och önskat resultat.
- Håll prompts fria från credentials och orelaterad projektkontext. Testa felaktiga, duplicerade och främmande rapporter genom servicekontroller.
- Löpande produktreview kan behålla Active. Använd NeedsReview endast när den manuella processen faktiskt väntar på extern granskning; den statusen visas i Attention.

Produktens framtida prompt om två Workers ändrar inte den manuella utvecklingsprocessens regel om en feature i taget.
