# F40-r1: pågående faktiskt nativeprov

**Status:** mapp/egna sessionsverktyg godkända av användaren 2026-10-16. Samma
Coordinator är ACTIVE med verifierat ACK och verklig Integration är REGISTERED.
Inget nativeacceptansfall eller main-merge är slutgodkänt. [Faktiska ID:n/UI/bevis](F-40-native.json).

| Resurs | Aktuellt faktiskt läge |
| --- | --- |
| Repo | `/tmp/herdr-f40-r1-fixture-20261015/repo`, main `b4919eb13d1934ec5c7ecafd98c5d748f58a0d18` |
| Källa | `c6c7822980abdce66a599534081c4287142fbd99`;83 produktbindningar identiska med implementation bb452da |
| Konfiguration | SHA256 `f59484b22ffc7fd885162a17f67cbc417f9fd55e889375364c9a27faa686f46f`, privat operatorprofil |
| Egen server | `hc-f40-r1-20261015`, PID1615840/start155220692, kör fortfarande |
| Coordinator | run95b20746-e85a-4028-8e2e-f642a486beab, agenthc-c-e4089d995c8f4dac88f4a08a, panew1:p1 |
| Start | ACTIVE, verkligt ACK; SID `01a143de-70c8-71d3-9f24-11c5278e7517` |
| Integration/Worker | Integration REGISTERED, SID `01a143e0-68bb-7b33-a484-8761fc3e63b1`; Workerleverans påbörjas |
| Native board | Två riktiga separata epics och två Pending1/User105-tasks, se JSON; inga utvecklings-ID:n ersatta |
| Faktisk SDK-katalog | Root6, Integration16 (12 control +4 befintliga guardade legacy/status), Worker0 |
| Aktuell regression | 233 unika relevanta fall verifierade, [proveniens/alla nod-ID:n](../reviews/F-40-current.json) |

## Vad som återstår

Användaren godkände uttryckligen ”Ja, godkänn F40-r1”. Samma ursprungliga
server/agent/pane/cwd/config och verkliga dialog verifierades före journalfört
Trust-val. Samma startavsikt gav verkligt ACK, utan promptreplay eller ny run.
Den riktiga F34-Integrationen är registrerad och manuellt attached genom F39:s
produktprovrutt före Workerstart. Dess separata uppdateringsdialog hoppades över
för att behålla Codex0.162.1. Native Integration har fått ett journalfört uppdrag
att leverera första hälsningsfilen genom verkliga scoped tjänster.

En eventuell avgränsad fixture-mainleverans kräver sedan ett separat konkret
operatörsgodkännande av den riktiga review/source/main-bindningen. F40-tjänsten ska
utföra faktisk F08-merge, F13-stopp, sluttest och board-Done. Nästa native claim
ska visa WAIT vid saknad D02/F17-grind. Generell autonomi eller en positiv
OS-säkerhetsgrind intygas inte av den manuella provauktorisationen.

Historik före samtycke: det första startanropets generiska TEAMPLAYER_CONNECTION_FAILED återlästes som
Folder trust/AGENT_PENDING. Ingen blind ny start, fabricerad session eller ny run
skapades. Då bevarades den namngivna servern och ursprungliga pendingdialogen inför
användarbeslut. Efter samtycke återupptogs samma start; alla data/refs bevaras. Projektets main och E10 påverkas inte av
fixtureprovets senare eventuella mainmerge.

Utvecklings-F40 är Testing11/Active0 av3. Kodcheckpoints är pushade på
`task/e10-f40`; ingen READY_FOR_REVIEW, Task→E10-merge eller Done är utförd.
E10 är fortsatt Active2 av3, F41 väntar på beroendet och sin runtimegrind.
