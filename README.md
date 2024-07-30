# Energiesystem Simulation und Optimierung

Dieses Projekt bietet eine umfassende Lösung zur Simulation und Optimierung eines Energiesystems, das auf erneuerbaren Energiequellen basiert. Mit Fokus auf Photovoltaik (PV)-Anlagen, Batteriespeichern (Akkus), Lastmanagement (Verbraucheranforderungen), Wärmepumpen, Elektrofahrzeugen und der Berücksichtigung von Strompreisdaten ermöglicht dieses System die Vorhersage und Optimierung des Energieflusses und der Kosten über einen bestimmten Zeitraum.

## Todo
- `Backend:` Mehr Optimierungsparameter
- `Frontend:` User Management
- `Frontend:` Grafische Ausgabe
- `Frontend:` Speichern von User Einstellungen (PV Anlage usw.)
- `Frontend:` Festeingestellte E-Autos / Wärmepumpen in DB
- `Simulation:` Wärmepumpe allgemeineren Ansatz
- `Simulation:` Strompreisvorhersage > 1D (Timeseries Forecast)
- `Simulation:` Lastverteilung 1h Werte -> Minuten (Tabelle) 
- `Dynamische Lasten:` z.B. eine Spülmaschine, welche gesteuert werdeb jabb,
- `Simulation:` AC Chargen möglich
- `Optimierung:` E-Auto Akku voll = in der 0/1 Liste keine Möglichkeit mehr auf 1 (aktuell ist der Optimierung das egalm ändert ja nichts) Optimierungsparameter reduzieren
- `Backend:` Visual Cleaner (z.B. E-Auto Akku = 100%, dann sollte die Lademöglichkeit auf 0 stehen. Zumindest bei der Ausgabe sollte das "sauber" sein)
- `Backend:` Cache regelmäßig leeren können (API)



## Installation

Das Projekt erfordert Python 3.8 oder neuer. Alle notwendigen Abhängigkeiten können über `pip` installiert werden. Klonen Sie das Repository und installieren Sie die erforderlichen Pakete mit:

```bash
git clone [URL des Repositories]
cd [Projektverzeichnis]
pip install -r requirements.txt
```

## PV Prognose API
Für die nötige PV Prognose bitte folgende API nutzen: 
https://api.akkudoktor.net/

## Nutzung

Um das System zu nutzen, führen Sie `test.py` aus, das eine Simulation für einen vorgegebenen Zeitraum durchführt. Die Konfiguration der Simulation, einschließlich der Vorhersagedaten und der Systemparameter, kann in den jeweiligen Klassen angepasst werden.

```bash
python test.py
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
- **Länge**: bis zu 48 

### gesamtlast
- **Beschreibung**: Ein Array von Floats, das die Gesamtlast (Verbrauch) in Watt für verschiedene Zeitintervalle darstellt.
- **Typ**: Array
- **Element-Typ**: Float
- **Länge**: bis zu 48

### pv_forecast
- **Beschreibung**: Ein Array von Floats, das die prognostizierte Photovoltaik-Leistung in Watt für verschiedene Zeitintervalle darstellt.
- **Typ**: Array
- **Element-Typ**: Float
- **Länge**: bis zu 48

### temperature_forecast
- **Beschreibung**: Ein Array von Floats, das die Temperaturvorhersage in Grad Celsius für verschiedene Zeitintervalle darstellt.
- **Typ**: Array
- **Element-Typ**: Float
- **Länge**: bis zu 48

### pv_soc
- **Beschreibung**: Ein Integer, der den aktuellen Ladezustand (State of Charge) der Photovoltaikanlage in Prozent darstellt.
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

Dieses Dokument beschreibt die Struktur und Datentypen des JSON-Outputs, den der Flask-Server zurückgibt.






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
    - **Länge**: bis zu 48
  - **Eigenverbrauch_Wh_pro_Stunde**: Ein Array von Floats, das den Eigenverbrauch in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
  - **Einnahmen_Euro_pro_Stunde**: Ein Array von Floats, das die Einnahmen in Euro pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
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
    - **Länge**: bis zu 48
  - **Kosten_Euro_pro_Stunde**: Ein Array von Floats, das die Kosten in Euro pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
  - **Netzbezug_Wh_pro_Stunde**: Ein Array von Floats, das den Netzbezug in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
  - **Netzeinspeisung_Wh_pro_Stunde**: Ein Array von Floats, das die Netzeinspeisung in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
  - **Verluste_Pro_Stunde**: Ein Array von Floats, das die Verluste pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
  - **akku_soc_pro_stunde**: Ein Array von Floats, das den Ladezustand des Akkus in Prozent pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48

### simulation_data
- **Beschreibung**: Ein Objekt, das die simulierten Daten enthält.
  - **E-Auto_SoC_pro_Stunde**: Ein Array von Floats, das den simulierten Ladezustand des Elektroautos pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
  - **Eigenverbrauch_Wh_pro_Stunde**: Ein Array von Floats, das den simulierten Eigenverbrauch in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
  - **Einnahmen_Euro_pro_Stunde**: Ein Array von Floats, das die simulierten Einnahmen in Euro pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
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
    - **Länge**: bis zu 48
  - **Kosten_Euro_pro_Stunde**: Ein Array von Floats, das die simulierten Kosten in Euro pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
  - **Netzbezug_Wh_pro_Stunde**: Ein Array von Floats, das den simulierten Netzbezug in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
  - **Netzeinspeisung_Wh_pro_Stunde**: Ein Array von Floats, das die simulierte Netzeinspeisung in Wattstunden pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
  - **Verluste_Pro_Stunde**: Ein Array von Floats, das die simulierten Verluste pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48
  - **akku_soc_pro_stunde**: Ein Array von Floats, das den simulierten Ladezustand des Akkus in Prozent pro Stunde darstellt.
    - **Typ**: Array
    - **Element-Typ**: Float
    - **Länge**: bis zu 48

### spuelstart
- **Beschreibung**: Kann `null` sein oder ein Objekt enthalten, das den Spülstart darstellt (wenn vorhanden).
- **Typ**: null oder object

### start_solution
- **Beschreibung**: Ein Array von Binärwerten (0 oder 1), das eine mögliche Startlösung für die Simulation darstellt.
- **Typ**: Array
- **Element-Typ**: Integer (0 oder




