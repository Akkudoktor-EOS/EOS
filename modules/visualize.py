import numpy as np
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.pyplot as plt
from modules.class_load_container import Gesamtlast  # Stellen Sie sicher, dass dies dem tatsächlichen Importpfad entspricht




def visualisiere_ergebnisse(gesamtlast,leistung_haushalt,leistung_wp, pv_forecast, strompreise, ergebnisse):
   

    # Last und PV-Erzeugung
    plt.figure(figsize=(14, 10))
    
    plt.subplot(3, 1, 1)
    stunden = np.arange(1, len(next(iter(gesamtlast.lasten.values()))) + 1)
    
    
    # Einzellasten plotten
    for name, last_array in gesamtlast.lasten.items():
        plt.plot(stunden, last_array, label=f'{name} (Wh)', marker='o')
    
    # Gesamtlast berechnen und plotten
    gesamtlast_array = gesamtlast.gesamtlast_berechnen()
    plt.plot(stunden, gesamtlast_array, label='Gesamtlast (Wh)', marker='o', linewidth=2, linestyle='--')
    
    plt.xlabel('Stunde')
    plt.ylabel('Last (Wh)')
    plt.title('Lastprofile')
    plt.grid(True)
    plt.legend()

    # Strompreise
    stundenp = np.arange(1, len(strompreise)+1)
    plt.subplot(3, 1, 2)
    plt.plot(stundenp, strompreise, label='Strompreis (€/Wh)', color='purple', marker='s')
    plt.title('Strompreise')
    plt.xlabel('Stunde des Tages')
    plt.ylabel('Preis (€/Wh)')
    plt.legend()
    plt.grid(True)


    plt.figure(figsize=(18, 12))
    stunden = np.arange(1, len(ergebnisse['Eigenverbrauch_Wh_pro_Stunde'])+1)
    # Eigenverbrauch, Netzeinspeisung und Netzbezug
    plt.subplot(3, 2, 1)
    plt.plot(stunden, ergebnisse['Eigenverbrauch_Wh_pro_Stunde'], label='Eigenverbrauch (Wh)', marker='o')
    plt.plot(stunden, ergebnisse['Netzeinspeisung_Wh_pro_Stunde'], label='Netzeinspeisung (Wh)', marker='x')
    plt.plot(stunden, ergebnisse['akku_soc_pro_stunde'], label='Akku (%)', marker='x')
    plt.plot(stunden, ergebnisse['Netzbezug_Wh_pro_Stunde'], label='Netzbezug (Wh)', marker='^')
    #plt.plot(stunden, pv_forecast, label='PV-Erzeugung (Wh)', marker='x')
    #plt.plot(stunden, last, label='Last (Wh)', marker='o')
    
    plt.title('Energiefluss pro Stunde')
    plt.xlabel('Stunde')
    plt.ylabel('Energie (Wh)')
    plt.legend()
    plt.grid(True)

    # Kosten und Einnahmen pro Stunde
    plt.subplot(3, 2, 2)
    plt.plot(stunden, ergebnisse['Kosten_Euro_pro_Stunde'], label='Kosten (Euro)', marker='o', color='red')
    plt.plot(stunden, ergebnisse['Einnahmen_Euro_pro_Stunde'], label='Einnahmen (Euro)', marker='x', color='green')
    plt.title('Finanzielle Bilanz pro Stunde')
    plt.xlabel('Stunde')
    plt.ylabel('Euro')
    plt.legend()
    plt.grid(True)

    # Zusammenfassende Finanzen
    plt.subplot(3, 2, 3)
    gesamtkosten = ergebnisse['Gesamtkosten_Euro']
    gesamteinnahmen = ergebnisse['Gesamteinnahmen_Euro']
    gesamtbilanz = ergebnisse['Gesamtbilanz_Euro']
    plt.bar('GesamtKosten', gesamtkosten, color='red' if gesamtkosten > 0 else 'green')
    plt.bar('GesamtEinnahmen', gesamteinnahmen, color='red' if gesamtkosten > 0 else 'green')
    plt.bar('GesamtBilanz', gesamtbilanz, color='red' if gesamtkosten > 0 else 'green')
    
    plt.title('Gesamtkosten')
    plt.ylabel('Euro')


    plt.legend()
    plt.grid(True)

    plt.tight_layout()
    plt.show()

