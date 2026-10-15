# F40 verifierad kod, väntande nativegodkännande

Kodcheckpoint bb452da; testorakel/metadata c6c7822.83 produkt-/policy-/konfigurations-
bindningar är byte-identiska. F40 är **NeedsApproval9 / Attention0/3**,
User105/blitterbot@gmail.com; backlogg och TeamPlayer är återlästa/synkroniserade.
E10 är fortsatt Active10/2 av3. F39 Done9 är integrerad och efterverifierad.

## Ändringar

- [CoordinatorDeliveryService](../../src/orchestrator/application/coordinator_delivery_service.py):
  beständig aktuell approval→F08-merge→F13-stopp→sluttest→Done/synk/release→F38-claim.
- [Claim](../../src/orchestrator/application/coordinator_claim_service.py),
  [review](../../src/orchestrator/application/coordinator_review_service.py) och
  [control](../../src/orchestrator/application/coordinator_control_service.py):
  effektiv korrigeringsplan, separata epickontexter och ändligt registrerat policyset.
- [MCP-kontrakt](../../src/orchestrator/mcp/contracts.py)/
  [server](../../src/orchestrator/mcp/server.py),
  [policy](../../prompts/coordinator-delivery-v1.md), README och paketkonfiguration.
- [Leveransprov](../../tests/test_coordinator_delivery.py),
  [två-epic-prov](../../tests/test_coordinator_delivery_project.py), generiska
  befintliga testhjälpare och faktisk nativevärd/probedriver.

## Faktisk verifiering

233 unika relevanta fall är verifierade över61 godkända från avbruten körning,
rättat1 PASS2.99s och resterande171 PASS871.35s. En ogiltig tilldelning till ett
redan fryst modellfält i det nya testet rättades till kapslad listförändring;
produktionen ändrades inte. Inget233-i-en-körning- eller fullprojekt-suitepåstående.
[Alla faktiska nod-ID:n/källor/kommandon/avstämning](F-40-current.json),
[171-JUnit](F-40-remaining.xml), [rättat fall](F-40-lock-corrected.xml),
[ursprungligt fel](F-40-current-interrupted.xml).

Lint, build, READMECLI-check, exakt wheel74moduler/sexpolicies och isolerad
wheelimport/policy passerade. Factual stdio-catalog: Root6, Integration16
(12 control +4 guardade befintliga legacy/statusposter), Worker0.

## Konkret kvarvarande steg

[Det verkliga F40-r1-provet](../coordinator/F-40-native.md) väntar vid Codex
”Trust this folder?” för `/tmp/herdr-f40-r1-fixture-20261015/repo`.
Användaren har tillfrågats om mapp/förväntade egna sessionsverktyg. Servern och
ursprungliga Root-startavsikten är bevarade, inga Integration-/Worker-runs,
slots eller mainmerges finns. Inget Native-SID/ACK har fabricerats.

Efter samtycke: återuppta samma startavsikt, genomför verklig native leverans och
review, få exakt reviewbunden begränsad fixture-mainauktorisation, verifiera
F40-effekter/stopp, slutgranska och integrera utvecklings-F40 till E10 med efterprov.
Först då kan F40 bli Done. D02/F17:s generella autonomigrind består inför F41.
