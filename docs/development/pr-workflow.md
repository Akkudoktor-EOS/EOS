# EOS: Arbeitsstand und Weg zu kleinen PRs

Stand: 16.09.2026. Offizielles main: `7ebe6d7`.

## Was jetzt möglich ist

Ein unabhängiger, lokal getesteter PR ist vorbereitet: `fix/measurement-json-reload`
auf aktuellem main. Sein Worktree ist `../EOS-pr-measurement-json`; er enthält nur
zwei geänderte Dateien. Der PR-Text steht in
[measurement-json-reload.md](pr-drafts/measurement-json-reload.md).

Das ist ein konkreter Einstieg in den PR-Workflow. Die vollständige Übernahme aller
Funktionen aus dem alten Feature-Branch ist noch NICHT abgeschlossen.

## Branches und ihre Aufgaben

| Branch | Zweck | Freigabezustand |
| --- | --- | --- |
| `fix/measurement-json-reload` | Kleiner JSON-Ladefehler direkt auf main | Lokal geprüft, Veröffentlichung ausstehend |
| `feat/config-integration-base` | Zusammengeführte #1256/#1305 plus Integrationskorrekturen | Lokale Abhängigkeitsbasis; kein konkurrierender Sammel-PR |
| `feat/measurement-energy-quality-capacity` | Messdatenfunktionen ohne neue Optimiererphysik | Getestet; wartet für main auf Konfigurationsbasis |
| `integration/eos-consolidation-20260916` | Zusammenführung und Prüfung aller Portierungspakete | Unvollständig; kein Gesamt-PR und kein HA-Release |
| `feat/direct-marketing-battery-grid-export` | Ursprüngliche Entwicklung mit lokalen Änderungen | Unverändert erhalten und gesichert |

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
   #1256 verbinden. Beide vorhandenen Funktionssätze müssen erhalten bleiben.
3. Horizont, Prognoselücken, Nachlauf und Restwert mit Optimierer und Ergebnissen
   verdrahten; bisher sind nur die Bausteine übernommen.
4. Tarifschutz aus #1224/#1304 und seine Regressionstests auf den neuen
   asynchronen Vorbereitungsweg übertragen.
5. Lokale kalibrierte PV-Prognose, algorithmusspezifische PDF-Ausgabe und die lokale
   Konfigurationslern-Anfrage integrieren.
6. Gesamtabnahme einschließlich API-Weg des neuen GENETIC und gepinnter CI.
   Danach erst Übergabe eines festen EOS-Commits an HA und Release-Arbeiten.

## PR-Reihenfolge

1. Den unabhängigen JSON-Fix nach Veröffentlichungsfreigabe gegen main einreichen.
2. #1256/#1305 über ihre vorhandenen PRs zusammenführen; lokale Korrekturen dort
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
Die ursprüngliche Arbeitskopie bleibt erhalten; nichts wurde deployt oder veröffentlicht.

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
Linux/Python 3.13, alle gepinnten Abhängigkeiten und die vollständige CI sind damit
nicht bestätigt. Die PRs sind lokal vorbereitet, noch nicht auf GitHub veröffentlicht.
