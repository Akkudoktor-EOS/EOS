# Tail-Optimierung in Grafana debuggen

Ziel sind drei vorhandene, zeitlich übereinanderliegende Panels:

1. **Strompreis** – welcher wirtschaftliche Anreiz besteht?
2. **Steuerplan** – welche Betriebsart wurde gewählt?
3. **Batterie-SoC** – was bewirkt die Entscheidung?

Die ersten 24 Stunden sind der echte Steuerplan. Danach folgt der intern
optimierte Tail. Der Tail ist ausschließlich Diagnose und wird nicht als
Steuerbefehl ausgegeben.

> Im Grafana Query Editor alle Namen ohne Backslashes eingeben. Richtig ist
> `eos_tail_plan`, nicht `eos\_tail\_plan`. Auch vor `$__timeFilter` und
> `$__timeGroupAlias` steht kein Backslash.

## 1. Prüfen, ob die neue EOS-Version läuft

EOS neu starten und einmal `/optimize` ausführen. In der vollständigen Antwort
muss `terminal_value.tail_plan` vorhanden und gefüllt sein.

Bei 15-Minuten-Intervallen enthält es normalerweise 192 Einträge bei 48 Stunden
Tail oder 190 Einträge bei einem auf 47,5 Stunden gekürzten Tail. Fehlt das Feld
oder ist es `[]`, läuft noch die alte EOS-Version. Dann kann Node-RED nichts für
Grafana speichern.

## 2. MariaDB-Tabelle einmalig anlegen

Das SQL aus [`nodered_tail_plan_schema.sql`](nodered_tail_plan_schema.sql)
einmal in der Datenbank `sensor` ausführen. Danach prüfen:

```sql
SHOW TABLES LIKE 'eos_tail_plan';
```

Es muss eine Zeile mit `eos_tail_plan` erscheinen.

## 3. Nur einen Node-RED-Node ändern

1. Den Function-Node **Tail + Terminalwert speichern** öffnen.
2. Ausschließlich dessen Funktionsinhalt ersetzen.
3. Den Inhalt aus
   [`nodered_tail_plan_function.js`](nodered_tail_plan_function.js) verwenden.
4. **Done** und danach **Deploy** drücken.
5. Die EOS-Optimierung erneut ausführen.

## 4. Vor Grafana prüfen, ob Node-RED Daten geschrieben hat

Diese Abfrage direkt in Grafana Explore ausführen. Format: `Table`.

```sql
SELECT
  COUNT(*) AS "Tail-Zeilen",
  MAX(run_ts) AS "Letzter Lauf",
  MIN(timestamp) AS "Tail beginnt",
  MAX(timestamp) AS "Letzter Tail-Slot"
FROM eos_tail_plan;
```

Erwartet werden 192 Zeilen für einen vollständigen Lauf beziehungsweise 190
Zeilen für 47,5 Stunden. Steht dort `0`, liegt das Problem noch bei EOS oder
Node-RED. Dann kann keine Grafana-Zeitabfrage Daten zeigen.

Zur Kontrolle der Inhalte:

```sql
SELECT
  run_ts,
  timestamp,
  slot,
  action,
  soc_start_pct,
  soc_end_pct,
  import_price_euro_kwh
FROM eos_tail_plan
ORDER BY run_ts DESC, slot
LIMIT 10;
```

## 5. Dashboard-Zeitbereich einstellen

Oben rechts:

```text
From: now-2h
To:   now+72h
```

Panels mit Ist-Daten wie Tesla, Wärmepumpen und Temperaturen behalten ihre
kurzen Panel-Zeitbereiche.

## 6. Panel „Strompreis“ erweitern

Im vorhandenen Strompreis-Panel eine Query hinzufügen. Format: `Time series`.

```sql
SELECT
  $__timeGroupAlias(timestamp,$__interval),
  AVG(import_price_euro_kwh) AS "Preis Tail – nur Bewertung"
FROM eos_tail_plan
WHERE
  $__timeFilter(timestamp)
  AND run_ts = (SELECT MAX(run_ts) FROM eos_tail_plan)
GROUP BY 1
ORDER BY 1;
```

Für die Tail-Linie Farbe Gelb, Line style `Dashes`, Line width `2` und Fill
opacity `0` einstellen. Die Werte sind bereits in €/kWh. Kein weiteres
`* 1000` anwenden.

## 7. Panel „Steuerplan“ erweitern

Die vorhandenen Zeilen `Disch`, `Spuel`, `AC`, `DC` und `DV` sind einzelne
0/1-Steuersignale des ausführbaren 24-h-Plans. Der Tail hat dagegen genau eine
gewählte Betriebsart pro Slot. Deshalb wird er als **eine zusätzliche
Text-Zeile** dargestellt und nicht auf die vorhandenen 0/1-Zeilen verteilt.

Im State-Timeline-Panel eine neue Query hinzufügen. Format: `Table`.

```sql
SELECT
  timestamp AS time,
  CASE action
    WHEN 'HOLD'              THEN 'Halten'
    WHEN 'PV_CHARGE_ONLY'    THEN 'Nur PV laden'
    WHEN 'SELF_CONSUMPTION'  THEN 'Haus aus PV/Akku'
    WHEN 'DISCHARGE_ONLY'    THEN 'Akku entladen'
    WHEN 'GRID_CHARGE'       THEN 'Akku aus Netz laden'
    WHEN 'BATTERY_EXPORT'    THEN 'Akku ins Netz verkaufen'
    ELSE 'Unbekannt'
  END AS "Tail – gedachte Entscheidung"
FROM eos_tail_plan
WHERE
  $__timeFilter(timestamp)
  AND run_ts = (SELECT MAX(run_ts) FROM eos_tail_plan)
ORDER BY timestamp;
```

`Merge equal consecutive values` einschalten und `Show values` auf `Always`
setzen. Für das Feld `Tail – gedachte Entscheidung` Value mappings mit Farben
anlegen: Netzladen blau, Batterieverkauf orange, Haus aus PV/Akku grün,
Nur-PV-Laden türkis, Entladen gelb und Halten grau. Weil die Abfrage bereits
verständliche Texte zurückgibt, dienen die Mappings nur noch der Farbe.

Die Tail-Zeile bleibt während der ersten 24 Stunden absichtlich leer. Sie
beginnt erst an der Grenze zum Tail. In den Standard options `No value` leeren,
damit Grafana für diesen Abschnitt nicht `-1` im Tooltip anzeigt.

## 8. Panel „Batterie SoC / Current“ erweitern

Neue Query, Format `Time series`:

```sql
SELECT
  $__timeGroupAlias(timestamp,$__interval),
  AVG(soc_end_pct) AS "SoC Tail – nur Bewertung"
FROM eos_tail_plan
WHERE
  $__timeFilter(timestamp)
  AND run_ts = (SELECT MAX(run_ts) FROM eos_tail_plan)
GROUP BY 1
ORDER BY 1;
```

Override für `SoC Tail – nur Bewertung`: Einheit `Percent (0-100)`, dieselbe
Achse wie der bisherige Prognose-SoC, gestrichelte hellblaue Linie, Breite 2,
Punkte aus.

## 9. Panel „Bezug & Einspeisung“ erweitern

Zuerst die Batterieleistung ergänzen:

```sql
SELECT
  $__timeGroupAlias(p.timestamp,$__interval),
  AVG(
    (p.battery_charge_wh - p.battery_discharge_wh)
    / NULLIF(v.data, 0)
  ) AS "Akku Tail (+ Laden / - Entladen)"
FROM eos_tail_plan p
JOIN eos_terminal_value v
  ON v.run_ts = p.run_ts
 AND v.topic = 'tail_slot_hours'
WHERE
  $__timeFilter(p.timestamp)
  AND p.run_ts = (SELECT MAX(run_ts) FROM eos_tail_plan)
GROUP BY 1
ORDER BY 1;
```

Diese Reihe beschreibt den Akku, nicht den Netzanschluss. Positive Werte sind
Ladung, negative Werte Entladung. Vorhandene PV kann auch direkt die Last
decken oder ins Netz fließen und erzeugt deshalb nicht zwingend einen positiven
Batteriewert.

Tail-Netzbezug, Format `Time series`:

```sql
SELECT
  $__timeGroupAlias(p.timestamp,$__interval),
  AVG(p.grid_import_wh / NULLIF(v.data, 0)) AS "Bezug Tail"
FROM eos_tail_plan p
JOIN eos_terminal_value v
  ON v.run_ts = p.run_ts
 AND v.topic = 'tail_slot_hours'
WHERE
  $__timeFilter(p.timestamp)
  AND p.run_ts = (SELECT MAX(run_ts) FROM eos_tail_plan)
GROUP BY 1
ORDER BY 1;
```

Tail-Einspeisung, Format `Time series`:

```sql
SELECT
  $__timeGroupAlias(p.timestamp,$__interval),
  -AVG(p.grid_export_wh / NULLIF(v.data, 0)) AS "Einspeisung Tail"
FROM eos_tail_plan p
JOIN eos_terminal_value v
  ON v.run_ts = p.run_ts
 AND v.topic = 'tail_slot_hours'
WHERE
  $__timeFilter(p.timestamp)
  AND p.run_ts = (SELECT MAX(run_ts) FROM eos_tail_plan)
GROUP BY 1
ORDER BY 1;
```

Beide Tail-Reihen gestrichelt darstellen. Positive Werte sind Bezug, negative
Werte Einspeisung.

## 10. Grenzen der drei Bereiche markieren

Unter **Dashboard settings → Annotations → Add annotation query**:

```sql
SELECT
  MIN(timestamp) AS time,
  'Tail beginnt – ab hier nur Bewertung' AS text
FROM eos_tail_plan
WHERE run_ts = (SELECT MAX(run_ts) FROM eos_tail_plan)

UNION ALL

SELECT
  DATE_ADD(MAX(timestamp), INTERVAL 15 MINUTE) AS time,
  'Prognoseende – danach greift der Restwert' AS text
FROM eos_tail_plan
WHERE run_ts = (SELECT MAX(run_ts) FROM eos_tail_plan);
```

Die erste senkrechte Linie trennt die reale Steuerung vom Tail. Die zweite
Linie markiert den Terminalpunkt.

Unter **Dashboard settings → General → Graph tooltip** zusätzlich `Shared
crosshair` wählen. Beim Überfahren eines Zeitpunkts steht der Cursor dann in
Strompreis, Steuerplan, SoC, PV sowie Bezug/Einspeisung an derselben Stelle.
Das macht einzelne Entscheidungen wesentlich leichter nachvollziehbar.

## 11. Kleines Panel „Wie eindeutig war die Entscheidung?“

Den bisherigen zeitunabhängigen Terminalwert-Kurvenplot durch ein kleines
`Time series`-Panel ersetzen:

```sql
SELECT
  timestamp AS time,
  decision_margin_euro * 100 AS "Vorsprung vor Alternative"
FROM eos_tail_plan
WHERE
  $__timeFilter(timestamp)
  AND run_ts = (SELECT MAX(run_ts) FROM eos_tail_plan)
ORDER BY timestamp;
```

Einheit: `Currency → Cent`, Minimum `0`, Linienbreite `2`, Punkte `Auto`.
Der Wert vergleicht die gewählte Betriebsart einschließlich ihrer späteren
Folgen mit der besten anders benannten Betriebsart. Nahe `0 ct` war die Wahl
fast gleichwertig und kann schon durch kleine Prognoseänderungen umspringen.
Ein größerer Wert bedeutet eine robuste Entscheidung.

## 12. Verdächtige Entscheidungen prüfen

Für einzelne Lade- oder Entladeereignisse ein temporäres Table-Panel anlegen:

```sql
SELECT
  timestamp AS "Zeit",
  CASE action
    WHEN 'GRID_CHARGE'      THEN 'Netzladen'
    WHEN 'BATTERY_EXPORT'   THEN 'Batterieverkauf'
    WHEN 'SELF_CONSUMPTION' THEN 'Normalbetrieb'
    WHEN 'PV_CHARGE_ONLY'   THEN 'PV-Laden'
    WHEN 'DISCHARGE_ONLY'   THEN 'Entladen'
    ELSE 'Halten'
  END AS "Gewählt",
  alternative_action AS "Beste andere Betriebsart",
  ROUND(import_price_euro_kwh, 3) AS "Preis €/kWh",
  ROUND(soc_start_pct, 1) AS "SoC vorher %",
  ROUND(soc_end_pct, 1) AS "SoC nachher %",
  ROUND(battery_charge_wh, 0) AS "Akku geladen Wh",
  ROUND(battery_discharge_wh, 0) AS "Akku entladen Wh",
  ROUND(grid_import_wh, 0) AS "Netzbezug Wh",
  ROUND(grid_export_wh, 0) AS "Einspeisung Wh",
  ROUND(decision_margin_euro * 100, 3) AS "Vorteil ct"
FROM eos_tail_plan
WHERE run_ts = (SELECT MAX(run_ts) FROM eos_tail_plan)
ORDER BY timestamp;
```

`Vorteil ct` deutlich positiv bedeutet, dass die Aktion einschließlich der
späteren Slots besser als die beste andere Betriebsart war. Ein Wert nahe null
zeigt einen nahezu gleichwertigen und damit instabilen Entschluss. Fällt der
SoC bei positiver Einspeisung, wurde Energie verkauft. Fällt er ohne
Einspeisung bei vorhandener Last, versorgt die Batterie das Haus. Fällt der SoC
ohne `battery_discharge_wh`, besteht ein Fehler in Pfad oder Zeitzuordnung.

## 13. Empfohlene kompakte Debug-Ansicht

Für das Verständnis reichen fünf übereinander ausgerichtete Zeitreihen:

1. `Strompreis` – wirtschaftlicher Anreiz.
2. `PV und Last` – verfügbare und benötigte Energie.
3. `Steuerplan` – fünf ausführbare 0/1-Zeilen plus eine Tail-Textzeile.
4. `Batterie-SoC` – Wirkung auf den Energiespeicher.
5. `Bezug & Einspeisung` – Wirkung am Netzanschluss.

Daneben oder darunter genügt das kleine Panel `Wie eindeutig war die
Entscheidung?`. Die Informationstabelle kann auf `24 h Steuerung`, `48 h Tail`,
`Prognose vollständig` und `Prognoseende` verkürzt werden. Der alte Plot der
Terminalwert-Kurve über Batterie-kWh ist für die tägliche Fehlersuche entbehrlich:
Er ist keine Zeitprognose, sondern nur die interne Bewertung möglicher
Restladungen am Prognoseende.

## 14. Keine erfundene PV-Prognose hinter dem Provider-Ende verwenden

`PVForecastForecastSolar` liefert im geprüften Lauf echte Werte nur bis zum
Abend des Folgetags. Der Endpoint `/v1/prediction/list` interpoliert trotzdem
bis zum angefragten Ende und erzeugt dadurch kleine scheinbare PV-Werte. Diese
Werte dürfen nicht als echter 72-h-Forecast in den Tail gelangen.

In Node-RED genau zwei Function-Nodes ändern:

1. **Prediction URL: PV 72h** durch den Inhalt von
   [`nodered_pv_series_url_function.js`](nodered_pv_series_url_function.js)
   ersetzen.
2. Den direkt hinter **PV read** liegenden bisherigen Node **rename** durch den
   Inhalt von
   [`nodered_pv_series_to_slots_function.js`](nodered_pv_series_to_slots_function.js)
   ersetzen und in **PV series -> echte 15-min-Slots** umbenennen.

Diese Variante interpoliert nur zwischen tatsächlich vorhandenen
Provider-Zeitpunkten. Hinter dem letzten echten Wert liefert sie `null`. EOS
verkürzt den Tail dann sichtbar, statt mit einer erfundenen Rest-PV zu rechnen.

Für einen vollständigen 48-h-Tail muss der PV-Provider mindestens 72 Stunden
ab jetzt liefern. Dafür `pvforecast.provider` beispielsweise auf
`PVForecastAkkudoktor` oder auf den mit API-Zugang konfigurierten
`PVForecastSolcast` umstellen. Mit `PVForecastForecastSolar` ist ein vollständiger
rollender 72-h-PV-Horizont nicht gewährleistet.
