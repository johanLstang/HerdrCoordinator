# F40-r1: faktisk nativeförberedelse och väntande godkännande

**Status:** väntar på faktisk Codex Folder access-dialog. Inget nativeacceptansfall
eller main-merge är slutgodkänt. [Faktiska ID:n/UI/bevis](F-40-native.json).

| Resurs | Aktuellt faktiskt läge |
| --- | --- |
| Repo | `/tmp/herdr-f40-r1-fixture-20261015/repo`, main `b4919eb13d1934ec5c7ecafd98c5d748f58a0d18` |
| Källa | `c6c7822980abdce66a599534081c4287142fbd99`;83 produktbindningar identiska med implementation bb452da |
| Konfiguration | SHA256 `f59484b22ffc7fd885162a17f67cbc417f9fd55e889375364c9a27faa686f46f`, privat operatorprofil |
| Egen server | `hc-f40-r1-20261015`, PID1615840/start155220692, kör fortfarande |
| Coordinator | run95b20746-e85a-4028-8e2e-f642a486beab, agenthc-c-e4089d995c8f4dac88f4a08a, panew1:p1 |
| Start | STARTING/AGENT_PENDING, faktisk Folder trust blockerar; inget SID/ACK ännu |
| Integration/Worker | Inga sådana runs eller sessioner startade; inga Worker-slots reserverade |
| Native board | Två riktiga separata epics och två Pending1/User105-tasks, se JSON; inga utvecklings-ID:n ersatta |
| Faktisk SDK-katalog | Root6, Integration16 (12 control +4 befintliga guardade legacy/status), Worker0 |
| Aktuell regression | 233 unika relevanta fall verifierade, [proveniens/alla nod-ID:n](../reviews/F-40-current.json) |

## Vad som återstår

Användaren har tillfrågats om att lita på detta ofarliga provrepo och godkänna de
egna F40-r1-sessionernas verktyg. Inget val skickas innan svaret. Därefter kan
operatören återuppta samma startavsikt, verifiera dess riktiga ACK och starta
verklig F34-Integration. Native Integration/Worker ska leverera hälsningsfilen,
genomföra review/Task→Epic/eftertest/fysisk stopp och F36-handoff; native Coordinator
ska läsa hela aktuella underlaget och göra sitt verkliga F39-reviewbeslut.

En eventuell avgränsad fixture-mainleverans kräver sedan ett separat konkret
operatörsgodkännande av den riktiga review/source/main-bindningen. F40-tjänsten ska
utföra faktisk F08-merge, F13-stopp, sluttest och board-Done. Nästa native claim
ska visa WAIT vid saknad D02/F17-grind. Generell autonomi eller en positiv
OS-säkerhetsgrind intygas inte av den manuella provauktorisationen.

Det första startanropets generiska TEAMPLAYER_CONNECTION_FAILED återlästes som
Folder trust/AGENT_PENDING. Ingen blind ny start, fabricerad session eller ny run
skapades. Den namngivna servern och ursprungliga pendingdialogen lämnas kvar inför
användarbeslut; alla data/refs bevaras. Projektets main och E10 påverkas inte av
fixtureprovets senare eventuella mainmerge.

Utvecklings-F40 är NeedsApproval/Attention0 av3. Kodcheckpoints är pushade på
`task/e10-f40`; ingen READY_FOR_REVIEW, Task→E10-merge eller Done är utförd.
E10 är fortsatt Active2 av3, F41 väntar på beroendet och sin runtimegrind.
