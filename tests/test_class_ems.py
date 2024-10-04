import pytest
import numpy as np
from modules.class_ems import EnergieManagementSystem
from modules.class_akku import PVAkku
from modules.class_haushaltsgeraet import Haushaltsgeraet  # Beispiel-Import
from modules.class_inverter import Wechselrichter  # Beispiel-Import
from modules.class_load_container import Gesamtlast  # Beispiel-Import

prediction_hours = 48
optimization_hours = 24

# Beispielhafte Initialisierungen der notwendigen Komponenten
@pytest.fixture
def create_ems_instance():
    """
    Fixture zur Erstellung einer EnergieManagementSystem-Instanz mit den gegebenen Testparametern.
    """
    # Initialisiere den Akku und den Wechselrichter
    akku = PVAkku(kapazitaet_wh=5000, start_soc_prozent=80, hours=48)
    akku.reset()
    wechselrichter = Wechselrichter(10000, akku)
    
    # Haushaltsgerät (aktuell nicht verwendet, daher auf None gesetzt)
    haushaltsgeraet = None
    
    # Beispielhafte Initialisierung von E-Auto
    eauto = PVAkku(kapazitaet_wh=26400, start_soc_prozent=10, hours=48)
    
    # Parameter aus den vorherigen Beispiel-Daten
    pv_prognose_wh = [
        0, 0, 0, 0, 0, 0, 0, 8.05, 352.91, 728.51, 930.28, 1043.25, 1106.74, 1161.69, 
        6018.82, 5519.07, 3969.88, 3017.96, 1943.07, 1007.17, 319.67, 7.88, 0, 0, 0, 0, 
        0, 0, 0, 0, 0, 5.04, 335.59, 705.32, 1121.12, 1604.79, 2157.38, 1433.25, 5718.49, 
        4553.96, 3027.55, 2574.46, 1720.4, 963.4, 383.3, 0, 0, 0,
    ]
    
    strompreis_euro_pro_wh = [
        0.0003384, 0.0003318, 0.0003284, 0.0003283, 0.0003289, 0.0003334, 0.0003290, 
        0.0003302, 0.0003042, 0.0002430, 0.0002280, 0.0002212, 0.0002093, 0.0001879, 
        0.0001838, 0.0002004, 0.0002198, 0.0002270, 0.0002997, 0.0003195, 0.0003081, 
        0.0002969, 0.0002921, 0.0002780, 0.0003384, 0.0003318, 0.0003284, 0.0003283, 
        0.0003289, 0.0003334, 0.0003290, 0.0003302, 0.0003042, 0.0002430, 0.0002280, 
        0.0002212, 0.0002093, 0.0001879, 0.0001838, 0.0002004, 0.0002198, 0.0002270, 
        0.0002997, 0.0003195, 0.0003081, 0.0002969, 0.0002921, 0.0002780,
    ]
    
    einspeiseverguetung_euro_pro_wh = [0.00007] * len(strompreis_euro_pro_wh)

    gesamtlast = [
        676.71, 876.19, 527.13, 468.88, 531.38, 517.95, 483.15, 472.28,
        1011.68, 995.00, 1053.07, 1063.91, 1320.56, 1132.03, 1163.67, 1176.82,
        1216.22, 1103.78, 1129.12, 1178.71, 1050.98, 988.56, 912.38, 704.61,
        516.37, 868.05, 694.34, 608.79, 556.31, 488.89, 506.91, 804.89,
        1141.98, 1056.97, 992.46, 1155.99, 827.01, 1257.98, 1232.67, 871.26,
        860.88, 1158.03, 1222.72, 1221.04, 949.99, 987.01, 733.99, 592.97,
    ]

    # Initialisiere das Energiemanagementsystem mit den entsprechenden Parametern
    ems = EnergieManagementSystem(
        pv_prognose_wh=pv_prognose_wh,
        strompreis_euro_pro_wh=strompreis_euro_pro_wh,
        einspeiseverguetung_euro_pro_wh=einspeiseverguetung_euro_pro_wh,
        eauto=eauto,
        gesamtlast=gesamtlast,
        haushaltsgeraet=haushaltsgeraet,
        wechselrichter=wechselrichter,
    )
    return ems



def test_simulation(create_ems_instance):
    """
    Test the EnergieManagementSystem simulation method.
    """
    ems = create_ems_instance
    
    # Simuliere ab Stunde 1 (dieser Wert kann angepasst werden)
    start_stunde = 1
    ergebnis = ems.simuliere(start_stunde=start_stunde)
    
    # Assertions to validate results
    assert ergebnis is not None, "Ergebnis should not be None"
    assert isinstance(ergebnis, dict), "Ergebnis should be a dictionary"
    assert "Last_Wh_pro_Stunde" in ergebnis, "Ergebnis should contain 'Last_Wh_pro_Stunde'"
    
    """
    Überprüft das Ergebnis der Simulation basierend auf den erwarteten Werten.
    """
    # Beispielergebnis, das von der Simulation zurückgegeben wurde (hier für die Assertions verwendet)
    assert ergebnis is not None, "Das Ergebnis sollte nicht None sein."
    
    # Überprüfe, dass das Ergebnis ein Dictionary ist
    assert isinstance(ergebnis, dict), "Das Ergebnis sollte ein Dictionary sein."
    
    # Überprüfe, dass die erwarteten Schlüssel im Ergebnis vorhanden sind
    expected_keys = [
        'Last_Wh_pro_Stunde', 'Netzeinspeisung_Wh_pro_Stunde', 'Netzbezug_Wh_pro_Stunde',
        'Kosten_Euro_pro_Stunde', 'akku_soc_pro_stunde', 'Einnahmen_Euro_pro_Stunde',
        'Gesamtbilanz_Euro', 'E-Auto_SoC_pro_Stunde', 'Gesamteinnahmen_Euro', 
        'Gesamtkosten_Euro', 'Verluste_Pro_Stunde', 'Gesamt_Verluste', 
        'Haushaltsgeraet_wh_pro_stunde'
    ]
    
    for key in expected_keys:
        assert key in ergebnis, f"Der Schlüssel '{key}' sollte im Ergebnis enthalten sein."
    
    # Überprüfe die Länge der wichtigsten Arrays
    assert len(ergebnis['Last_Wh_pro_Stunde']) == 47, "Die Länge von 'Last_Wh_pro_Stunde' sollte 48 betragen."
    assert len(ergebnis['Netzeinspeisung_Wh_pro_Stunde']) == 47, "Die Länge von 'Netzeinspeisung_Wh_pro_Stunde' sollte 48 betragen."
    assert len(ergebnis['Netzbezug_Wh_pro_Stunde']) == 47, "Die Länge von 'Netzbezug_Wh_pro_Stunde' sollte 48 betragen."
    assert len(ergebnis['Kosten_Euro_pro_Stunde']) == 47, "Die Länge von 'Kosten_Euro_pro_Stunde' sollte 48 betragen."
    assert len(ergebnis['akku_soc_pro_stunde']) == 47, "Die Länge von 'akku_soc_pro_stunde' sollte 48 betragen."
    
    # Überprüfe spezifische Werte im 'Last_Wh_pro_Stunde' Array
    assert ergebnis['Last_Wh_pro_Stunde'][1] == 23759.13, "Der Wert an Index 1 von 'Last_Wh_pro_Stunde' sollte 23759.13 sein."
    assert ergebnis['Last_Wh_pro_Stunde'][2] == 996.88, "Der Wert an Index 2 von 'Last_Wh_pro_Stunde' sollte 996.88 sein."
    assert ergebnis['Last_Wh_pro_Stunde'][12] == 1132.03, "Der Wert an Index 12 von 'Last_Wh_pro_Stunde' sollte 1132.03 sein."
    
    # Überprüfe, dass der Wert bei Index 0 'None' ist
    assert ergebnis['Last_Wh_pro_Stunde'][0] is None, "Der Wert an Index 0 von 'Last_Wh_pro_Stunde' sollte None sein."
    
    # Überprüfe, dass 'Netzeinspeisung_Wh_pro_Stunde' und 'Netzbezug_Wh_pro_Stunde' konsistent sind
    assert ergebnis['Netzeinspeisung_Wh_pro_Stunde'][0] is None, "Der Wert an Index 0 von 'Netzeinspeisung_Wh_pro_Stunde' sollte None sein."
    assert ergebnis['Netzbezug_Wh_pro_Stunde'][0] is None, "Der Wert an Index 0 von 'Netzbezug_Wh_pro_Stunde' sollte None sein."
    assert ergebnis['Netzbezug_Wh_pro_Stunde'][1] == 20239.13, "Der Wert an Index 1 von 'Netzbezug_Wh_pro_Stunde' sollte 20239.13 sein."

    # Überprüfe die Gesamtbilanz
    assert abs(ergebnis['Gesamtbilanz_Euro'] - 8.434942129454546) < 1e-5, "Die Gesamtbilanz sollte 8.434942129454546 betragen."

    # Überprüfe die Gesamteinnahmen und Gesamtkosten
    assert abs(ergebnis['Gesamteinnahmen_Euro'] - 1.237432954545454) < 1e-5, "Die Gesamteinnahmen sollten 1.237432954545454 betragen."
    assert abs(ergebnis['Gesamtkosten_Euro'] - 9.672375084) < 1e-5, "Die Gesamtkosten sollten 9.672375084 betragen."

    # Überprüfe die Verluste
    assert abs(ergebnis['Gesamt_Verluste'] - 6111.586363636363) < 1e-5, "Die Gesamtverluste sollten 6111.586363636363 betragen."

    # Überprüfe die Werte im 'akku_soc_pro_stunde'
    assert ergebnis['akku_soc_pro_stunde'][-1] == 28.675, "Der Wert an Index -1 von 'akku_soc_pro_stunde' sollte 28.675 sein."
    assert ergebnis['akku_soc_pro_stunde'][1] == 0.0, "Der Wert an Index 1 von 'akku_soc_pro_stunde' sollte 0.0 sein."

    print("Alle Tests erfolgreich bestanden.")