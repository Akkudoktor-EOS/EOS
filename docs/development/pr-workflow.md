# EOS: Arbeitsstand und Weg zu kleinen PRs

Stand: 16.09.2026. Offizielles main für PR #1322: `4a37244`.
Die lokale Integration enthält ebenfalls diesen main-Stand und den aktualisierten
Stand von #1305 (`60b77f6`).

## Was jetzt möglich ist

Ein unabhängiger, lokal getesteter PR ist veröffentlicht: `fix/measurement-json-reload`
auf aktuellem main. Sein Worktree ist `../EOS-pr-measurement-json`; er enthält nur
zwei geänderte Dateien. Der veröffentlichte [PR #1322](https://github.com/Akkudoktor-EOS/EOS/pull/1322)
enthält zwei Commits bis `ce132ea`. Der PR-Text steht in
[measurement-json-reload.md](pr-drafts/measurement-json-reload.md).

Das ist ein konkreter Einstieg in den PR-Workflow. Die vollständige Übernahme aller
Funktionen aus dem alten Feature-Branch ist noch NICHT abgeschlossen.

## Branches und ihre Aufgaben

<!-- pyml disable line-length -->
| Branch | Zweck | Freigabezustand |
| --- | --- | --- |
| `fix/measurement-json-reload` | Kleiner JSON-Ladefehler direkt auf main | Veröffentlicht als #1322; gesamte CI grün, noch nicht gemergt |
| `feat/config-integration-base` | Zusammengeführte #1256/#1305 plus Integrationskorrekturen | Veröffentlichter Vergleichsbranch 9038b65; kein konkurrierender Sammel-PR |
| `feat/measurement-energy-quality-capacity` | Messdatenfunktionen ohne neue Optimiererphysik | PR #1326 gegen Konfigurationsbasis; später auf main umstellen |
| `fix/imported-feedin-main` | Importierte Einspeisetarife erhalten und prüfen | Veröffentlicht als #1324; CI läuft |
| `feat/local-pv-main-port` | Lokale PV-Prognose und Kalibrierung | PR #1325 gegen main |
| `feat/slot-device-physics` | Slotphysik und getrennte Cache-Methoden | PR #1327 gegen Konfigurationsbasis; später auf main umstellen |
| `fix/optimize-run-result` | Nur das Ergebnis des erfolgreichen aktuellen Laufs zurückgeben | Veröffentlicht als #1323; CI läuft |
| `integration/eos-consolidation-20260916` | Zusammenführung und Prüfung aller Portierungspakete | Unvollständig; kein Gesamt-PR und kein HA-Release |
| `feat/direct-marketing-battery-grid-export` | Ursprüngliche Entwicklung mit lokalen Änderungen | Unverändert erhalten und gesichert |
<!-- pyml enable line-length -->

Der Worktree `../EOS-pr-measurement` gehört zum Messdatenpaket.
`../EOS-integration-20260916` bleibt der zeitlich begrenzte Portierungsarbeitsplatz.
`../EOS-reference-20260916` bleibt der unveränderte Vergleichsstand für Basisfehler.
Ein Worktree ist nur ein Arbeitsverzeichnis; Gegenstand eines PRs ist der Branch.

## Umgesetzt und geprüft

- Geräte-Maps, Algorithmuskonvertierung und Laufzeitkonfiguration aus #1256/#1305.
- Korrekturen für stabile Geräte-IDs, LCOS-Migration und Ladeleistungslisten.
- Messkanäle, Qualität, Energieintegration, Haushaltsbilanz und Kapazitätsschätzung,
  einschließlich asynchroner Speicherung und REST-Schnittstellen.
- Im Integrationsbranch außerdem Viertelstunden-Gerätephysik, begrenzter
  Batterieexport und Wirkungsgrade; Restwert-/Prognose-Nachlauf-Bausteine.
- GENETIC0 bleibt separat. Sein `/optimize`-Endpunkt beweist keine vollständige
  Portierung des neuen GENETIC.

## Noch offen

1. Neues GENETIC vollständig auf main-Strukturen anpassen: Viertelstundenplanung,
   Exportzustände, adaptive Evolution und zeitlich korrekter Warmstart.
2. Flexible Lastprofile/EV-Fristen mit den Mehrfachzyklen und Zeitfenstern aus
   `#1256` verbinden. Beide vorhandenen Funktionssätze müssen erhalten bleiben.
3. Horizont, Prognoselücken, Nachlauf und Restwert mit Optimierer und Ergebnissen
   verdrahten; bisher sind nur die Bausteine übernommen.
4. Den bereits portierten Tarifschutz auch in der neuen GENETIC-Parametervorbereitung
   erhalten; dort Prognosegrenzen und Lücken verbindlich prüfen.
5. Algorithmusspezifische PDF-Ausgabe und die lokale Konfigurationslern-Anfrage
   integrieren. Die lokale kalibrierte PV-Prognose ist inzwischen portiert.
6. Gesamtabnahme einschließlich API-Weg des neuen GENETIC und gepinnter CI.
   Danach erst Übergabe eines festen EOS-Commits an HA und Release-Arbeiten.

## PR-Reihenfolge

1. JSON-Fix als PR #1322 veröffentlicht: CI und Review prüfen, danach über Merge entscheiden.
2. `#1256/#1305` über ihre vorhandenen PRs zusammenführen; lokale Korrekturen dort
   zuordnen. Keine pauschale Veröffentlichung der kombinierten Integrationsbasis.
3. Das isolierte Messdatenpaket auf diesen main-Stand setzen, Diff prüfen und
   nochmals testen; dann als eigenen PR einreichen.
4. Optimierer, Tarifschutz, PV und Ausgabe jeweils als abgegrenzte Pakete fertigstellen.
   Abhängige PRs ausdrücklich als solche behandeln.

Für jede neue unabhängige Änderung: aktuellen `refs/remotes/origin/main` holen,
einen Themenbranch mit eigenem Worktree starten, lokal testen, den Diff prüfen,
dann PR gegen main. Nach Review und grüner CI mergen. Alte Worktrees erst nach
abgeschlossener Übernahme und Prüfung lokaler Änderungen aufräumen.

Wegen der vorhandenen gleichnamigen lokalen Branch-Referenz ausdrücklich
`refs/remotes/origin/main` verwenden. Keine neuen unabhängigen Funktionen auf den
alten großen Feature-Branch oder die Integrationsbasis stapeln.

## Kann die laufende Entwicklung schon umziehen?

Unabhängige Fehlerkorrekturen und neue Funktionen können ab jetzt in Themenbranches
auf main erfolgen. Für Entwicklung, die den vollständigen neuen GENETIC oder die
noch fehlenden Funktionen benötigt, ist der Integrationsstand noch nicht abgenommen.
Die ursprüngliche Arbeitskopie bleibt erhalten. JSON-Fix #1322, Optimize-Fix #1323
und Tarifschutz #1324 sind veröffentlicht. Auf weitere Freigabe folgten PV #1325,
Messdaten #1326 und Gerätephysik #1327; nichts wurde gemergt oder deployt.
Der aktuelle Review-Überblick steht in [review-handoff.md](review-handoff.md).

## HA-Übergabe

Noch keinen neuen Gesamtstand pinnen oder deployen. Die geplante Schnittstelle nutzt
Geräte-Maps mit stabilen IDs, `levelized_cost_of_storage_amt_kwh`, asynchrone
Messdatenzugriffe und getrennte GENETIC/GENETIC0-Pfade. Details stehen in
[eos-ha-handoff.md](eos-ha-handoff.md). HA-Dateien wurden nicht verändert.

## Nachweise und Grenzen

Der JSON-PR: 49 bestandene Tests, Ruff und Formatprüfung.
Das isolierte Messdatenpaket: 453 bestandene Tests plus 5 Dokumentationstests;
74 Tests nach Übernahme der Fixture-Isolation nochmals erfolgreich.
XML-Protokolle liegen in der privaten Sicherung `eos-20260916-120324`.
Für PR #1322 ist die gepinnte Linux/Python-3.13-CI inzwischen bestätigt:
1.884 Tests bestanden, 16 übersprungen; Pre-commit/Mypy, CodeQL und Docker-Build
erfolgreich auf `ce132ea`. Die aktuellen CI-Ergebnisse aller sechs PRs stehen im Review-Handoff.

Zusätzlicher Integrationslauf: 764 Tests bestanden, 3 übersprungen; zwei zunächst
fehlgeschlagene Dokumentationsvergleiche betrafen ausschließlich die Versionsangabe.
Nach Neugenerierung bestanden alle 5 Dokumentationstests. Darunter sind außerdem
128 bestandene Energy-Charts-Regressionen zum neuen main-Commit dokumentiert.

Früherer gemeinsamer Source-Stand `b684748`: 277 Tests bestanden, 3 regulär
übersprungen, für PV, Tarifschutz, Gerätephysik, Cache, Konfiguration und beide
bisherigen Optimierer einschließlich API-Fehlerbehandlung. Anschließend bestanden
alle 132 Messdaten-/Haushalts-/Kapazitätsprüfungen auf diesem gemeinsamen Stand.
Die vollständige neue GENETIC-Orchestrierung bleibt offen. Pakete und
Kompatibilitätsbedingungen stehen in [pr-integration-matrix.md](pr-integration-matrix.md).
Die neu generierte gemeinsame Dokumentation besteht ebenfalls alle fünf Prüfungen.

Aktueller Abschluss: Alle sechs veröffentlichten PRs haben ihre vorgesehenen
GitHub-Prüfungen bestanden. Gemeinsamer Stand: 253 Dateien ohne Mypy-Fehler,
37 gezielte Nachprüfungen und vollständiger Sphinx-Build erfolgreich. Die Grenzen
des Windows-Gesamtlaufs und die nachgewiesenen main-Baselinefehler sind im
[Review-Handoff](review-handoff.md) dokumentiert. Nichts wurde gemergt oder deployt.
