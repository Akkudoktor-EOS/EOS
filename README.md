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

