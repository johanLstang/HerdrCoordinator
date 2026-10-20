# F38 READY_FOR_REVIEW

Task F-38 / 64b97b6e-2251-4964-9b67-f67d709de9bf, branch task/e10-f38.
Sekventiell bootstrap: Worker levererar kod; operatören har säkrat nativeprovet;
Integration gör nu aktuell review och eventuell Task→E10-merge. Manuella
utvecklingsworktrees adopteras aldrig till produktens run-/runtimeägarskap.

Kodcheckpoint b554e791, efterföljande fe083ade ändrade endast backlogg.
Aktuellt överlämnings-SHA registreras i det externa faktiska reviewbeslutet efter
att denna dokumentation och nativebevis committats.

- F38.A1: verklig Git/SQLite/F34, kontrollerad runtime/board/gate, prioritet och
  stabil ordning samt aktuell main; högt prioriterad körbar epic registreras.
- F38.A2: samtidiga anslutningar, databasunik owner/reservation och retry/reopen;
  tre reviewfynd om främmande aktiva resurser åtgärdade med10 regressioner.
- F38.A3: beroende-Done utan leveransbevis, olöst gate och tom scope nekar start.
  Faktisk original-Coordinator gav WAIT utan reserverad epic; fysiskt stoppad.

954 fullprov PASS2320.94s på4e, därefter41 aktuella claimprov PASS21.65s på
korrigerad kod. Övriga121 kod-/test-/policy-/konfigfiler är identiska. Ruff,
build, READMECLI och exakta67 wheelmoduler/fyra policies passerade.
[Fulla test-/källbindningar](F-38-source-4e.json), [korrigerad proof](F-38-correction.json),
[nativeprov och faktiska begränsningar](../coordinator/F-38-native.md).

Nativeprovet styrker start/ACK/samma-SID-reopen/nekande/stopp och gäller ursprunglig
checkpoint4e. Det ger ingen positiv OS-/autonomigrind. D02/F17/F41 kvarstår.
Agentens slutrapport-SID är null, faktisk SID kontrollerades oberoende från native
Herdr/Codex/persistens. Första stoppets wrapputfall avstämdes från faktisk
EXIT_PENDING; samma nyckel bekräftade inaktivitet utan en andra exit.

Slutreview, faktisk Task→E10 och efterkontroller krävs före Task Done.
