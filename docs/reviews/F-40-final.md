# F40: granskad, integrerad och efterverifierad

Bootstrap Integration godkände exakt task `44cb0ef34da331b996cec0b348c77af529837714`
mot E10 `4b4c08a7ae10ad06255ee6fac3a60f3d91c16b9e` och main
`b6ceb9956f512a2ed2330fe24b2cd741d83738fb`. Full diff/hash och review finns i
[maskinunderlaget](F-40-final.json). Ingen återstående blockerande reviewanmärkning.

GitAdapter under gemensam flock utförde faktisk no-ff Task→E10
`f4d6621a913dbf4771b0535b08514b387c98ecda`, operation
`cb902f17-9c4f-4874-8481-0b869389f5ea`, med exakta parents och identiskt granskat
träd. Bootstrap-undantaget är dokumenterat: manuella utvecklingsworktrees är
oregistrerade och adopterades aldrig som produktruns. Worker utförde ingen merge.

## Acceptans

- **A1:** verklig native current approval → en mainmerge6aab460 → fysisk stopp →
  sluttest exit0 → provepic Done4, inga för tidiga Done-/releaseeffekter.
- **A2:** original-Integration faktiskt stoppad; temporära två-epic-serviceprov
  verifierar verkligt nytt main-SHA/claim-release/nästa F34-registrering, även efter
  korrigering och förlorat startsvar. Native nästa start nekas korrekt av faktisk
  saknad generell säkerhetsgrind.
- **A3:** stale/owner/membership/sluttestfel/okänt utfall/sex avbrottsgränser och
  upprepade anrop passerar; två verkliga nativeanrop gav identiska kvitton och en
  merge/sluttest/stopp. Ingen ny native run/slot eller för tidig fortsättning.

[Fullständigt nativeunderlag](../coordinator/F-40-native-final.json) visar separata
användargodkännanden, riktig policy/review/merge/board och fysisk stängning av alla
egna runtimes/server. Det är ett begränsat manuellt registrerat produktprov,
inte en generell positiv OS-säkerhetsattest. Kontrollerade adaptrar anges uttryckligen.

## Verifiering på den faktiska E10-mergen

`uv run --locked pytest -q tests/test_coordinator_delivery.py tests/test_coordinator_delivery_project.py tests/test_mcp.py --junitxml=/tmp/herdr-f40-post.xml`
→ **35 PASS,1263.82s, exit0**; [JUnit](F-40-post.xml).
Ruff, build och READMECLI --check passerade. Alla83 produkt-/55 verifieringspins är
identiska; wheel innehåller exakt74moduler/sex deklarerade policies och samma
SHA2566937de3d som den tidigare isolerat verifierade wheelen. En första inventory-
kontroll inkluderade felaktigt prompts/AGENTS.md; observeroraklet rättades till de
sex deklarerade resurserna, ingen produktändring eller ombuild behövdes.

Tidigare233 relevanta fall verifierades i kvalificerade61+1+171 segment; råa
nod-ID:n, det korrigerade testoraklets historiska fel och verkliga JUnit-filer
bevaras i [källproveniensen](F-40-current.json). Ingen fullprojektsvit eller
233-i-en-körning påstås.

F40 är Done3/3 efter färsk TeamPlayer-CAS. E10 kvar Active2/3: F41:s fulla scenario,
faktiska generella runtimegrind och epicens mainintegration/slutreview återstår.

TeamPlayer återläst: F40 Done18/User105, E10 Active11. Task- och epicbeskrivning
är exakt synkroniserade med backloggen; ingen Git-effekt upprepades vid synk.
