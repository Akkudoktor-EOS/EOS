# Energiesystem Simulation und Optimierung

Dieses Projekt bietet eine umfassende Lösung zur Simulation und Optimierung eines Energiesystems, das auf erneuerbaren Energiequellen basiert. Mit Fokus auf Photovoltaik (PV)-Anlagen, Batteriespeichern (Akkus), Lastmanagement (Verbraucheranforderungen), Wärmepumpen, Elektrofahrzeugen und der Berücksichtigung von Strompreisdaten ermöglicht dieses System die Vorhersage und Optimierung des Energieflusses und der Kosten über einen bestimmten Zeitraum.

## Mitmachen

Die Diskussion findet im [Forum](https://www.akkudoktor.net/forum/diy-energie-optimierungssystem-opensource-projekt/) statt. Bugs bitte im [Issue Tracker](https://github.com/Akkudoktor-EOS/EOS/issues) melden, Code-Beiträge und Bug-Fixes nehmen wir gerne als [Pull-Requests](https://github.com/Akkudoktor-EOS/EOS/pulls) entgegen.

## Installation

Gute Install Anleitung:
https://meintechblog.de/2024/09/05/andreas-schmitz-joerg-installiert-mein-energieoptimierungssystem/

Das Projekt erfordert Python 3.8 oder neuer.

### Schnellanleitung

Unter Linux (Ubuntu/Debian):

```bash
sudo apt install make
```

Unter Macos (benötigt [Homebrew](https://brew.sh)):

```zsh
brew install make
```

Nun `config.py` anpassen.
Anschließend kann der Server über `make run` gestartet werden.
Eine vollständige Übersicht über die wichtigsten Kurzbefehle gibt `make help`.

### Ausführliche Anleitung

Alle notwendigen Abhängigkeiten können über `pip` installiert werden. Klonen Sie das Repository und installieren Sie die erforderlichen Pakete mit:

```bash
git clone https://github.com/Akkudoktor-EOS/EOS
cd EOS
```
Als Nächstes legen wir ein virtuelles Environment an. Es dient zur Ablage der Python-Abhängigkeiten,
die wir später per `pip` installieren:

```bash
virtualenv .venv
```

Schließlich installieren wir die Python-Abhängigkeiten von EOS:

```bash
.venv/bin/pip install -r requirements.txt
```

Um immer die Python-Version aus dem Virtual-Env zu verwenden, sollte vor der Arbeit in
EOS Folgendes aufgerufen werden:

```bash
source .venv/bin/activate
```

(für Bash-Nutzende, der Standard unter Linux) oder

```zsh
. .venv/bin/activate
```

(wenn zsh verwendet wird, vor allem MacOS-Nutzende).

Sollte `pip install` die mariadb-Abhängigkeit nicht installieren können,
dann helfen folgende Kommandos:

* Debian/Ubuntu: `sudo apt-get install -y libmariadb-dev`
* Macos/Homebrew: `brew install mariadb-connector-c`

gefolgt von einem erneuten `pip install -r requirements.txt`.

## Nutzung

Einstellungen in `config.py` anpassen.
Um das System zu nutzen, führen Sie `flask_server.py` aus, damit wird der Server gestartet


```bash
./flask_server.py
```
## Klassen und Funktionalitäten

In diesem Projekt werden verschiedene Klassen verwendet, um die Komponenten eines Energiesystems zu simulieren und zu optimieren. Jede Klasse repräsentiert einen spezifischen Aspekt des Systems, wie nachfolgend beschrieben:

- `PVAkku`: Simuliert einen Batteriespeicher, einschließlich der Kapazität, des Ladezustands und jetzt auch der Lade- und Entladeverluste.

- `PVForecast`: Stellt Vorhersagedaten für die Photovoltaik-Erzeugung bereit, basierend auf Wetterdaten und historischen Erzeugungsdaten.

- `Load`: Modelliert die Lastanforderungen des Haushalts oder Unternehmens, ermöglicht die Vorhersage des zukünftigen Energiebedarfs.

- `HeatPump`: Simuliert eine Wärmepumpe, einschließlich ihres Energieverbrauchs und ihrer Effizienz unter verschiedenen Betriebsbedingungen.

- `Strompreis`: Bietet Informationen zu den Strompreisen, ermöglicht die Optimierung des Energieverbrauchs und der -erzeugung basierend auf Tarifinformationen.

- `EMS`: Das Energiemanagementsystem (EMS) koordiniert die Interaktion zwischen den verschiedenen Komponenten, führt die Optimierung durch und simuliert den Betrieb des gesamten Energiesystems.

Diese Klassen arbeiten zusammen, um eine detaillierte Simulation und Optimierung des Energiesystems zu ermöglichen. Für jede Klasse können spezifische Parameter und Einstellungen angepasst werden, um verschiedene Szenarien und Strategien zu testen.

### Anpassung und Erweiterung

Jede Klasse ist so gestaltet, dass sie leicht angepasst und erweitert werden kann, um zusätzliche Funktionen oder Verbesserungen zu integrieren. Beispielsweise können neue Methoden zur genaueren Modellierung des Verhaltens von PV-Anlagen oder Batteriespeichern hinzugefügt werden. Entwickler sind eingeladen, das System nach ihren Bedürfnissen zu modifizieren und zu erweitern.


# Input für den Flask Server (Stand 30.07.204)
Beschreibt die Struktur und Datentypen des JSON-Objekts, das an den Flask-Server gesendet wird. Hier mit einem Prognosezeitraum von 48 Stunden!

## Felder des JSON-Objekts

### strompreis_euro_pro_wh
- **Beschreibung**: Ein Array von Floats, das den Strompreis in Euro pro Wattstunde für verschiedene Zeitintervalle darstellt.
- **Typ**: Array
- **Element-Typ**: Float
- **Länge**: 48

### gesamtlast
- **Beschreibung**: Ein Array von Floats, das die Gesamtlast (Verbrauch) in Watt für verschiedene Zeitintervalle darstellt.
- **Typ**: Array
- **Element-Typ**: Float
- **Länge**: 48

### pv_forecast
- **Beschreibung**: Ein Array von Floats, das die prognostizierte Photovoltaik-Leistung in Watt für verschiedene Zeitintervalle darstellt.
- **Typ**: Array
- **Element-Typ**: Float
- **Länge**: 48

### temperature_forecast
- **Beschreibung**: Ein Array von Floats, das die Temperaturvorhersage in Grad Celsius für verschiedene Zeitintervalle darstellt.
- **Typ**: Array
- **Element-Typ**: Float
- **Länge**: 48

### pv_soc
- **Beschreibung**: Ein Integer, der den Ladezustand des PV Akkus zum START der aktuellen Stunde anzeigt, das ist nicht der aktuelle!!!
- **Typ**: Integer

### pv_akku_cap
- **Beschreibung**: Ein Integer, der die Kapazität des Photovoltaik-Akkus in Wattstunden darstellt.
- **Typ**: Integer

### einspeiseverguetung_euro_pro_wh
- **Beschreibung**: Ein Float, der die Einspeisevergütung in Euro pro Wattstunde darstellt.
- **Typ**: Float

### eauto_min_soc
- **Beschreibung**: Ein Integer, der den minimalen Ladezustand (State of Charge) des Elektroautos in Prozent darstellt.
- **Typ**: Integer

### eauto_cap
- **Beschreibung**: Ein Integer, der die Kapazität des Elektroauto-Akkus in Wattstunden darstellt.
- **Typ**: Integer

### eauto_charge_efficiency
- **Beschreibung**: Ein Float, der die Ladeeffizienz des Elektroautos darstellt.
- **Typ**: Float

### eauto_charge_power
- **Beschreibung**: Ein Integer, der die Ladeleistung des Elektroautos in Watt darstellt.
- **Typ**: Integer

### eauto_soc
- **Beschreibung**: Ein Integer, der den aktuellen Ladezustand (State of Charge) des Elektroautos in Prozent darstellt.
- **Typ**: Integer

### start_solution
- **Beschreibung**: Kann null sein oder eine vorherige Lösung enthalten (wenn vorhanden).
- **Typ**: null oder object

### haushaltsgeraet_wh
- **Beschreibung**: Ein Integer, der den Energieverbrauch eines Haushaltsgeräts in Wattstunden darstellt.
- **Typ**: Integer

### haushaltsgeraet_dauer
- **Beschreibung**: Ein Integer, der die Dauer der Nutzung des Haushaltsgeräts in Stunden darstellt.
- **Typ**: Integer







# JSON-Output Beschreibung

Dieses Dokument beschreibt die Struktur und Datentypen des JSON-Outputs, den der Flask-Server zurückgibt. Hier mit einem Prognosezeitraum von 48h

## Felder des JSON-Outputs (Stand 30.7.2024)

### discharge_hours_bin
- **Beschreibung**: Ein Array von Binärwerten (0 oder 1), das anzeigt, ob in einer bestimmten Stunde Energie entladen wird.
- **Typ**: Array
- **Element-Typ**: Integer (0 oder 1)
- **Länge**: 48

### eauto_obj
- **Beschreibung**: Ein Objekt, das Informationen über das Elektroauto enthält.
  - **charge_array**: Ein Array von Binärwerten (0 oder 1), das anzeigt, ob das Elektroauto in einer bestimmten Stunde geladen wird.
    - **Typ**: Array
    - **Element-Typ**: Integer (0 oder 1)
    - **Länge**: 48
  - **discharge_array**: Ein Array von Binärwerten (0 oder 1), das anzeigt, ob das Elektroauto in einer bestimmten Stunde entladen wird.
    - **Typ**: Array
    - **Element-Typ**: Integer (0 oder 1)
    - **Länge**: 48
  - **entlade_effizienz**: Die Entladeeffizienz des Elektroautos.
    - **Typ**: Float
  - **hours**: Die Anzahl der Stunden, für die die Simulation durchgeführt wird.
    - **Typ**: Integer
  - **kapazitaet_wh**: Die Kapazität des Elektroauto-Akkus in Wattstunden.
    - **Typ**: Integer
  - **lade_effizienz**: Die Ladeeffizienz des Elektroautos.
    - **Typ**: Float
  - **max_ladeleistung_w**: Die maximale Ladeleistung des Elektroautos in Watt.
    - **Typ**: Integer
  - **soc_wh**: Der Ladezustand (State of Charge) des Elektroautos in Wattstunden.
    - **Typ**: Integer
  - **start_soc_prozent**: Der initiale Ladezustand (State of Charge) des Elektroautos in Prozent.
    - **Typ**: Integer

### eautocharge_hours_float
- **Beschreibung**: Ein Array von Binärwerten (0 oder 1), das anzeigt, ob das Elektroauto in einer bestimmten Stunde geladen wird.
- **Typ**: Array
- **Element-Typ**: Integer (0 oder 1)
- **Länge**: 48

### result
- **Beschreibung**: Ein Objekt, das die Ergebnisse der Simulation enthält.
  - **E-Auto_SoC_pro_Stunde**: Ein Array von Floats, das den Ladezustand des Elektroautos für jede Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Eigenverbrauch_Wh_pro_Stunde**: Ein Array von Floats, das den Eigenverbrauch in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Einnahmen_Euro_pro_Stunde**: Ein Array von Floats, das die Einnahmen in Euro pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Gesamt_Verluste**: Die gesamten Verluste in Wattstunden.
    - **Typ**: Float
  - **Gesamtbilanz_Euro**: Die gesamte Bilanz in Euro.
    - **Typ**: Float
  - **Gesamteinnahmen_Euro**: Die gesamten Einnahmen in Euro.
    - **Typ**: Float
  - **Gesamtkosten_Euro**: Die gesamten Kosten in Euro.
    - **Typ**: Float
  - **Haushaltsgeraet_wh_pro_stunde**: Ein Array von Floats, das den Energieverbrauch eines Haushaltsgeräts in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Kosten_Euro_pro_Stunde**: Ein Array von Floats, das die Kosten in Euro pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Netzbezug_Wh_pro_Stunde**: Ein Array von Floats, das den Netzbezug in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Netzeinspeisung_Wh_pro_Stunde**: Ein Array von Floats, das die Netzeinspeisung in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Verluste_Pro_Stunde**: Ein Array von Floats, das die Verluste pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **akku_soc_pro_stunde**: Ein Array von Floats, das den Ladezustand des Akkus in Prozent pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35

### simulation_data
- **Beschreibung**: Ein Objekt, das die simulierten Daten enthält.
  - **E-Auto_SoC_pro_Stunde**: Ein Array von Floats, das den simulierten Ladezustand des Elektroautos pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Eigenverbrauch_Wh_pro_Stunde**: Ein Array von Floats, das den simulierten Eigenverbrauch in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Einnahmen_Euro_pro_Stunde**: Ein Array von Floats, das die simulierten Einnahmen in Euro pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Gesamt_Verluste**: Die gesamten simulierten Verluste in Wattstunden.
    - **Typ**: Float
  - **Gesamtbilanz_Euro**: Die gesamte simulierte Bilanz in Euro.
    - **Typ**: Float
  - **Gesamteinnahmen_Euro**: Die gesamten simulierten Einnahmen in Euro.
    - **Typ**: Float
  - **Gesamtkosten_Euro**: Die gesamten simulierten Kosten in Euro.
    - **Typ**: Float
  - **Haushaltsgeraet_wh_pro_stunde**: Ein Array von Floats, das den simulierten Energieverbrauch eines Haushaltsgeräts in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Kosten_Euro_pro_Stunde**: Ein Array von Floats, das die simulierten Kosten in Euro pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Netzbezug_Wh_pro_Stunde**: Ein Array von Floats, das den simulierten Netzbezug in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Netzeinspeisung_Wh_pro_Stunde**: Ein Array von Floats, das die simulierte Netzeinspeisung in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **Verluste_Pro_Stunde**: Ein Array von Floats, das die simulierten Verluste pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35
  - **akku_soc_pro_stunde**: Ein Array von Floats, das den simulierten Ladezustand des Akkus in Prozent pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: 35

### spuelstart
- **Beschreibung**: Kann `null` sein oder ein Objekt enthalten, das den Spülstart darstellt (wenn vorhanden).
- **Typ**: null oder object

### start_solution
- **Beschreibung**: Ein Array von Binärwerten (0 oder 1), das eine mögliche Startlösung für die Simulation darstellt.
- **Typ**: Array
- **Element-Typ**: Integer (0 oder 1)
- **Länge**: 48
