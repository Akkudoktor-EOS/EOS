import numpy as np
from modules.class_load_container import Gesamtlast  # Stellen Sie sicher, dass dies dem tatsächlichen Importpfad entspricht
import matplotlib.pyplot as plt


def visualisiere_ergebnisse(gesamtlast, pv_forecast, strompreise, ergebnisse,  discharge_hours, laden_moeglich, temperature, start_hour, prediction_hours):

    #####################
    # 24h 
    #####################
    
    # Last und PV-Erzeugung
    plt.figure(figsize=(14, 14))
    plt.subplot(3, 2, 1)
    stunden = np.arange(0, prediction_hours)
    
    
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
    plt.subplot(3, 2, 2)
    plt.plot(stundenp, strompreise, label='Strompreis (€/Wh)', color='purple', marker='s')
    plt.title('Strompreise')
    plt.xlabel('Stunde des Tages')
    plt.ylabel('Preis (€/Wh)')
    plt.legend()
    plt.grid(True)

    # Strompreise
    stundenp = np.arange(1, len(strompreise)+1)
    plt.subplot(3, 2, 3)
    plt.plot(stunden, pv_forecast, label='PV-Erzeugung (Wh)', marker='x')
    plt.title('PV Forecast')
    plt.xlabel('Stunde des Tages')
    plt.ylabel('Wh')
    plt.legend()
    plt.grid(True)

    # Temperatur Forecast
    plt.subplot(3, 2, 4)
    plt.title('Temperatur Forecast °C')
    plt.plot(stunden, temperature, label='Temperatur °C', marker='x')
    plt.xlabel('Stunde des Tages')
    plt.ylabel('°C')
    plt.legend()
    plt.grid(True)


    #####################
    # Start_Hour  
    #####################    
    
    plt.figure(figsize=(14, 10))
    stunden = np.arange(start_hour, prediction_hours)
    # Eigenverbrauch, Netzeinspeisung und Netzbezug
    plt.subplot(3, 2, 1)
    plt.plot(stunden, ergebnisse['Eigenverbrauch_Wh_pro_Stunde'], label='Eigenverbrauch (Wh)', marker='o')
    plt.plot(stunden, ergebnisse['Netzeinspeisung_Wh_pro_Stunde'], label='Netzeinspeisung (Wh)', marker='x')
    plt.plot(stunden, ergebnisse['Netzbezug_Wh_pro_Stunde'], label='Netzbezug (Wh)', marker='^')
    plt.plot(stunden, ergebnisse['Verluste_Pro_Stunde'], label='Verluste (Wh)', marker='^')
    plt.title('Energiefluss pro Stunde')
    plt.xlabel('Stunde')
    plt.ylabel('Energie (Wh)')
    plt.legend()
    
    
    
    
    plt.subplot(3, 2, 2)
    plt.plot(stunden, ergebnisse['akku_soc_pro_stunde'], label='PV Akku (%)', marker='x')
    plt.plot(stunden, ergebnisse['E-Auto_SoC_pro_Stunde'], label='E-Auto Akku (%)', marker='x')
    plt.legend(loc='upper left')

    ax1 = plt.subplot(3, 2, 3)
    for hour, value in enumerate(discharge_hours):
        #if value == 1:
        ax1.axvspan(hour, hour+1, color='red',ymax=value, alpha=0.3, label='Entlademöglichkeit' if hour == 0 else "")
    for hour, value in enumerate(laden_moeglich):
        #if value == 1:
        ax1.axvspan(hour, hour+1, color='green',ymax=value, alpha=0.3, label='Lademöglichkeit' if hour == 0 else "")
    ax1.legend(loc='upper left')


    
    
    
    
    
    
    plt.grid(True)
    plt.figure(figsize=(14, 10))
    # Kosten und Einnahmen pro Stunde
    plt.subplot(1, 2, 1)
    plt.plot(stunden, ergebnisse['Kosten_Euro_pro_Stunde'], label='Kosten (Euro)', marker='o', color='red')
    plt.plot(stunden, ergebnisse['Einnahmen_Euro_pro_Stunde'], label='Einnahmen (Euro)', marker='x', color='green')
    plt.title('Finanzielle Bilanz pro Stunde')
    plt.xlabel('Stunde')
    plt.ylabel('Euro')
    plt.legend()
    

    # Zusammenfassende Finanzen
    fig, ax1 = plt.subplots()
    gesamtkosten = ergebnisse['Gesamtkosten_Euro']
    gesamteinnahmen = ergebnisse['Gesamteinnahmen_Euro']
    gesamtbilanz = ergebnisse['Gesamtbilanz_Euro']
    labels = ['GesamtKosten [€]', 'GesamtEinnahmen [€]', 'GesamtBilanz [€]']
    werte = [gesamtkosten, gesamteinnahmen, gesamtbilanz]
    colors = ['red' if wert > 0 else 'green' for wert in werte]

    ax1.bar(labels, werte, color=colors)
    ax1.set_ylabel('Euro')
    ax1.set_title('Finanzübersicht')

    # Zweite Achse (ax2) für die Verluste, geteilt mit ax1
    ax2 = ax1.twinx()
    verluste = ergebnisse['Gesamt_Verluste']
    ax2.bar('GesamtVerluste', verluste, color='blue')
    ax2.set_ylabel('Verluste [Wh]', color='blue')

    # Stellt sicher, dass die Achsenbeschriftungen der zweiten Achse in der gleichen Farbe angezeigt werden
    ax2.tick_params(axis='y', labelcolor='blue')

    
    # plt.title('Gesamtkosten')
    # plt.ylabel('Euro')


    # plt.legend()
    # plt.grid(True)

    # plt.tight_layout()
    plt.show()

