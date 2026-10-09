# F25 — beständig TeamPlayer-synk

## API och ansvar

`TeamPlayerSyncService` använder en operatörsöppnad, katalogverifierad
`teamplayer_connection(..., writable=True)`. Operatören registrerar projektets
externa UUID, autentiserad User och explicita `bind_epic`/`bind_task`-referenser.
Bindning kräver produktens registrerade F05-worktree; tasken ska ha en intakt
F15-specifikation. Native namn, kriterier, beroenden, epic och User-tilldelning
kontrolleras igen inför varje synk. Legacy agent-ID används inte som User-bevis.

`sync_task(actor, run_id)` kräver Integration; `sync_epic` kräver Coordinator.
Worker nekas innan nätanrop eller synkjournal skapas. Rollen kommer från en betrodd
registrerad anslutning. Agentens argument får inte välja roll, status, version,
extern UUID, kommentartext eller verifieringsbevis.

Internt MCP registrerar `set_task_status` och `set_epic_status` bara när operatören
har injicerat `teamplayer_sync` i `RuntimeService`. Anropet tar `project_id` och
exakt ett motsvarande run-ID. Status härleds från aktuell state och journal.
Den vanliga CLI-starten ansluter inte automatiskt till TeamPlayer och inga
credentials eller endpointvärden skickas till Workers. F39 inför Coordinator-loopen.

## Vad som får speglas

| Domänläge | TeamPlayer | Bevis som kontrolleras |
| --- | --- | --- |
| Task Planned/Claimed/Starting | Pending | Intakt lokal spec, registrerat ägarskap och aktuell domänhändelse; start är ännu inte bekräftad. |
| Task Working och senare aktiva reviewfaser | InProgress | F11-start, beständig F12-dispatch och native ACK med samma session, turn, item och promptdigest. |
| Task Blocked/Parked | NeedsInput | Konkret orsak från F16-rapport eller F13-stopp; parkering kräver fysisk inaktivitet, rätt runtimegeneration och frigjord slot. |
| Task Done | Done | F20-approval och reviewcontextdigests, faktisk F07 Task→Epic-merge med operationstagg och exakta föräldrar, godkänt test på merge-SHA samt F13-stopp före Done-händelsen. |
| Epic Planned | Pending | Lyckad F05-skapandeoperation och ingen startövergång. |
| Epic påbörjad, före Done | InProgress | Aktuell domänhändelse och registrerat worktree; alla tasks Done eller taskblockerare ändrar inte epicen till Done. |
| Epic Done | Done | Exakt operatörsregistrerat taskscope, samtliga faktiska taskleveranser, komplett accepterad epicchecklista, aktuell samlad review/test, faktisk Epic→main-merge och sluttest på merge-SHA. Även native taskscope och alla native tasks Done kontrolleras genom F24:s kompletta boardläsare. |

`EpicIntegrationService.register_epic_review` kan nu få `verified_criteria` från
betrodd Coordinator-review. En explicit godkänd checklista måste exakt motsvara
`Settings.review_context.acceptance_criteria`; den sparas på samma SHA-bundna
reviewoperation. Äldre reviews utan checklista kan fortfarande användas av F08,
men F25 vägrar skriva epic-Done från dem. Det skapar ingen framtida completionservice:
F40 behöver använda kontraktet när Coordinatorns slutreview implementeras.
Testoperatören gör dessa roller sekventiellt i dagens bootstrap.

Historisk task-Done kräver inte att en redan stoppad Codex-server startas igen.
Review-, Git-, test-, process- och Done-journalerna kontrolleras direkt. F13:s idempotenta stoppalias är tillåtna: parkering verifierar exakt det stopp-ID som dess domänhändelse namnger, och Done använder leveransens stopp-ID. Native
ACK behövs för aktiva faser. En speglad boardstatus är aldrig bevis för runtime
eller merge, och synktjänsten utför ingen start, test, stop, resume eller Git-mutation.

## Outbox och återförsök

Befintlig `operations`-tabell innehåller `kind=teamplayer_sync`; ingen migration
krävs. Nyckeln är SHA256 av run, objektslag och beständig domänhändelse. Intent
innehåller bindning, önskad status och sanerat verifieringsunderlag. Det sparas
innan första externa anropet. Versionsbas och full beskrivningsavsikt sparas
före varje skrivning. `ExternalReference` håller unika verifierade UUID-bindningar.

Ett exklusivt processlås per SQLite-fil serialiserar TeamPlayer-skrivningar.
En konkurrerande synk får `TEAMPLAYER_SYNC_BUSY`; kör om efter återläsning.
Operatören får inte köra flera aktiva restaurerade databaskopior mot samma
externa boardobjekt. F25-provet använder en kopia endast mot nya fixture-ID:n.

1. Verifiera konto och Write-grant; återläs objekt, tilldelning och kontrakt.
2. Återläs vid timeout. Redan önskad status räknas som speglad status, men lokal
   framgång kräver fortfarande intakta Git/runtimebevis och historikreadback.
3. Om den första CAS-skrivningen uttryckligen avvisades med `version_conflict`,
   får nästa anrop planera om med ny version efter ny kontraktskontroll.
4. Efter okänt utfall får samma oförändrade versionsbas återförsökas. Om versionen
   har avancerat utan avstämbar effekt lämnas `PENDING` med `OUTCOME_DIVERGED`.
   Ett senare CAS-avslag bevisar inte att en tidigare timeout aldrig applicerades.
5. Status och historik har separata delresultat. Återförsök bara saknat steg.
6. En ny domänhändelse ersätter äldre `PENDING`-avsikter med `SUPERSEDED`; alla
   tidigare försök bevaras. Den äldre statusen spelas inte upp igen.
7. `SUCCEEDED` kräver aktuell lokal källa, önskad native status och exakt
   historikreadback. Replay verifierar dessa och returnerar `EXISTING` utan write.

TeamPlayer har inget fristående kommentarverktyg. En logisk kommentar är därför
ett block i taskbeskrivningen med `herdr-event:<operation UUID>`, textdigest och
slutmarkör. Oförändrad manuell text bevaras. Dubbla/förändrade markörer eller en
borttagen redan bekräftad historikhändelse kräver avstämning; ingen blind append.
Manuella NeedsApproval/NeedsReview/Paused/Cancelled övertrumfas inte. Done-objekt
återöppnas inte automatiskt. Bekräftad F13-resume får NeedsInput/Blocked→InProgress.
Epicen kan aldrig backas från Active till Planned genom automatisk synk.

Credentials hämtas endast av betrodd operatorhelper/env och hålls i anslutningens
minne. Registrerade credential-envvärden och anslutningens header-/bearervärden
maskeras ur utgående historik. Fel visar fasta koder, inte råa svar eller headers.

## Verifiering och begränsningar

`tests/test_teamplayer_sync.py` använder riktiga temporära Git-repon, produktens
F15–F21-operationer och SQLite. Herdr/Codexhistorik samt TeamPlayer är kontrollerade
adaptrar. Nätfel injiceras före/efter status- och historikskrivning, samt vid lokala
checkpoints. Testerna jämför faktisk HEAD, antal merge/test/start/stop och skickade
prompter före/efter retry. MCP-provet använder riktig inprocess Client/Server och
registrerade roller. F25 ersätter inte F38:s säkerhets-/autonomigrind.

Nativeprov: `scripts/probes/f25_sync.py prepare|sync|verify`, separat testepic
`63cebf13-391f-4785-bdc4-e3e489cb25fa`, User
`105f26a7-0648-438d-94fd-3260ac3af4ee`, projekt HerdrCoordinator.
`prepare` gör en SQLite-backup av faktiska inaktiva F22-runs, vägrar ersätta
befintlig provhistorik, och behåller alla Git/runtimeidentiteter. Fixturecreation
är ett separat uttryckligt operatörssteg; scriptet får bara verifierade nya UUID:n.
Det ändrar aldrig den ursprungliga F22-journalen eller Git/runtime.

Det verkliga native Done-anropet följs av ett uttryckligt injicerat förlorat
klientsvar. Outbox blir Pending, återläsning slutför endast historiken, och replay
lämnar exakt en historikhändelse. Den andra faktiska F22-taskens verifierade
parkering speglas som NeedsInput. Epicen är Active eftersom den inte har en
main-leverans. [Native underlag](F-25-prover.json) redovisar IDs och digests.
En ny live Worker och hela Active→Attention→input→Active→Done provar F26.
