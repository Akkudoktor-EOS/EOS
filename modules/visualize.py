import numpy as np
from modules.class_sommerzeit import *
from modules.class_load_container import Gesamtlast  # Stellen Sie sicher, dass dies dem tatsächlichen Importpfad entspricht
import matplotlib
matplotlib.use('Agg')  # Setzt das Backend auf Agg

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from datetime import datetime


def visualisiere_ergebnisse(gesamtlast, pv_forecast, strompreise, ergebnisse,  discharge_hours, laden_moeglich, temperature, start_hour, prediction_hours,einspeiseverguetung_euro_pro_wh, filename="visualisierungsergebnisse.pdf", extra_data=None):

    #####################
    # 24h 
    #####################
    with PdfPages(filename) as pdf:

        
        # Last und PV-Erzeugung
        plt.figure(figsize=(14, 14))
        plt.subplot(3, 3, 1)
        stunden = np.arange(0, prediction_hours)
        
        gesamtlast_array = np.array(gesamtlast)
        # Einzellasten plotten
        #for name, last_array in gesamtlast.lasten.items():
        plt.plot(stunden, gesamtlast_array, label=f'Last (Wh)', marker='o')
        
        # Gesamtlast berechnen und plotten
        gesamtlast_array = np.array(gesamtlast)
        plt.plot(stunden, gesamtlast_array, label='Gesamtlast (Wh)', marker='o', linewidth=2, linestyle='--')
        plt.xlabel('Stunde')
        plt.ylabel('Last (Wh)')
        plt.title('Lastprofile')
        plt.grid(True)
        plt.legend()


        # Strompreise
        stundenp = np.arange(0, len(strompreise))
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

        # Vergütung
        stundenp = np.arange(0, len(strompreise))
        plt.subplot(3, 2, 4)
        plt.plot(stunden, einspeiseverguetung_euro_pro_wh, label='Vergütung €/Wh', marker='x')
        plt.title('Vergütung')
        plt.xlabel('Stunde des Tages')
        plt.ylabel('€/Wh')
        plt.legend()
        plt.grid(True)


        # Temperatur Forecast
        plt.subplot(3, 2, 5)
        plt.title('Temperatur Forecast °C')
        plt.plot(stunden, temperature, label='Temperatur °C', marker='x')
        plt.xlabel('Stunde des Tages')
        plt.ylabel('°C')
        plt.legend()
        plt.grid(True)

        pdf.savefig()  # Speichert den aktuellen Figure-State im PDF
        plt.close()  # Schließt die aktuelle Figure, um Speicher freizugeben


        #####################
        # Start_Hour  
        #####################    
        
        plt.figure(figsize=(14, 10))
        
        if ist_dst_wechsel(datetime.now()):
                stunden = np.arange(start_hour, prediction_hours-1)
        else:
                stunden = np.arange(start_hour, prediction_hours)
        
        print(ist_dst_wechsel(datetime.now())," ",datetime.now())
        print(start_hour," ",prediction_hours," ",stunden)
        print(ergebnisse['Eigenverbrauch_Wh_pro_Stunde'])
        # Eigenverbrauch, Netzeinspeisung und Netzbezug
        plt.subplot(3, 2, 1)
        plt.plot(stunden, ergebnisse['Eigenverbrauch_Wh_pro_Stunde'], label='Eigenverbrauch (Wh)', marker='o')
        plt.plot(stunden, ergebnisse['Haushaltsgeraet_wh_pro_stunde'], label='Haushaltsgerät (Wh)', marker='o')
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
            print(hour)
            ax1.axvspan(hour, hour+1, color='red',ymax=value, alpha=0.3, label='Entlademöglichkeit' if hour == 0 else "")
        for hour, value in enumerate(laden_moeglich):
            #if value == 1:
            ax1.axvspan(hour, hour+1, color='green',ymax=value, alpha=0.3, label='Lademöglichkeit' if hour == 0 else "")
        ax1.legend(loc='upper left')


        pdf.savefig()  # Speichert den aktuellen Figure-State im PDF
        plt.close()  # Schließt die aktuelle Figure, um Speicher freizugeben
        
        
        
        
        
        
        
        plt.grid(True)
        fig, axs = plt.subplots(1, 2, figsize=(14, 10))  # Erstellt 1x2 Raster von Subplots
        gesamtkosten = ergebnisse['Gesamtkosten_Euro']
        gesamteinnahmen = ergebnisse['Gesamteinnahmen_Euro']
        gesamtbilanz = ergebnisse['Gesamtbilanz_Euro']
        verluste = ergebnisse['Gesamt_Verluste']

        # Kosten und Einnahmen pro Stunde auf der ersten Achse (axs[0])
        axs[0].plot(stunden, ergebnisse['Kosten_Euro_pro_Stunde'], label='Kosten (Euro)', marker='o', color='red')
        axs[0].plot(stunden, ergebnisse['Einnahmen_Euro_pro_Stunde'], label='Einnahmen (Euro)', marker='x', color='green')
        axs[0].set_title('Finanzielle Bilanz pro Stunde')
        axs[0].set_xlabel('Stunde')
        axs[0].set_ylabel('Euro')
        axs[0].legend()
        axs[0].grid(True)

        # Zusammenfassende Finanzen auf der zweiten Achse (axs[1])
        labels = ['GesamtKosten [€]', 'GesamtEinnahmen [€]', 'GesamtBilanz [€]']
        werte = [gesamtkosten, gesamteinnahmen, gesamtbilanz]
        colors = ['red' if wert > 0 else 'green' for wert in werte]
        axs[1].bar(labels, werte, color=colors)
        axs[1].set_title('Finanzübersicht')
        axs[1].set_ylabel('Euro')

        # Zweite Achse (ax2) für die Verluste, geteilt mit axs[1]
        ax2 = axs[1].twinx()
        ax2.bar('GesamtVerluste', verluste, color='blue')
        ax2.set_ylabel('Verluste [Wh]', color='blue')
        ax2.tick_params(axis='y', labelcolor='blue')

        pdf.savefig()  # Speichert die komplette Figure im PDF
        plt.close()  # Schließt die Figure
        
        

        if extra_data != None:
                plt.figure(figsize=(14, 10))
                plt.subplot(1, 2, 1)
                f1 = np.array(extra_data["verluste"])
                f2 = np.array(extra_data["bilanz"])
                n1 = np.array(extra_data["nebenbedingung"])
                scatter = plt.scatter(f1, f2, c=n1, cmap='viridis')

                # Farblegende hinzufügen
                plt.colorbar(scatter, label='Nebenbedingung')

                pdf.savefig()  # Speichert die komplette Figure im PDF
                plt.close()  # Schließt die Figure

                
                plt.figure(figsize=(14, 10))
                filtered_verluste = np.array([v for v, n in zip(extra_data["verluste"], extra_data["nebenbedingung"]) if n < 0.01])
                filtered_bilanz = np.array([b for b, n in zip(extra_data["bilanz"], extra_data["nebenbedingung"]) if n< 0.01])
                
                beste_verluste = min(filtered_verluste)
                schlechteste_verluste = max(filtered_verluste)
                beste_bilanz = min(filtered_bilanz)
                schlechteste_bilanz = max(filtered_bilanz)

                data = [filtered_verluste, filtered_bilanz]
                labels = ['Verluste', 'Bilanz']
                # Plot-Erstellung
                fig, axs = plt.subplots(1, 2, figsize=(10, 6), sharey=False)  # Zwei Subplots, getrennte y-Achsen

                # Erster Boxplot für Verluste
                #axs[0].boxplot(data[0])
                axs[0].violinplot(data[0],
                          showmeans=True,
                          showmedians=True)
                axs[0].set_title('Verluste')
                axs[0].set_xticklabels(['Verluste'])

                # Zweiter Boxplot für Bilanz
                axs[1].violinplot(data[1],
                          showmeans=True,
                          showmedians=True)
                axs[1].set_title('Bilanz')
                axs[1].set_xticklabels(['Bilanz'])

                # Feinabstimmung
                plt.tight_layout()
        
        
        pdf.savefig()  # Speichert den aktuellen Figure-State im PDF
        plt.close() 
        
        # plt.figure(figsize=(14, 10))
        # # Kosten und Einnahmen pro Stunde
        # plt.subplot(1, 2, 1)
        # plt.plot(stunden, ergebnisse['Kosten_Euro_pro_Stunde'], label='Kosten (Euro)', marker='o', color='red')
        # plt.plot(stunden, ergebnisse['Einnahmen_Euro_pro_Stunde'], label='Einnahmen (Euro)', marker='x', color='green')
        # plt.title('Finanzielle Bilanz pro Stunde')
        # plt.xlabel('Stunde')
        # plt.ylabel('Euro')
        # plt.legend()


        # plt.grid(True)
        # #plt.figure(figsize=(14, 10))
        # # Zusammenfassende Finanzen
        # #fig, ax1 = plt.subplot(1, 2, 2)
        # fig, ax1 = plt.subplots()
        # gesamtkosten = ergebnisse['Gesamtkosten_Euro']
        # gesamteinnahmen = ergebnisse['Gesamteinnahmen_Euro']
        # gesamtbilanz = ergebnisse['Gesamtbilanz_Euro']
        # labels = ['GesamtKosten [€]', 'GesamtEinnahmen [€]', 'GesamtBilanz [€]']
        # werte = [gesamtkosten, gesamteinnahmen, gesamtbilanz]
        # colors = ['red' if wert > 0 else 'green' for wert in werte]

        # ax1.bar(labels, werte, color=colors)
        # ax1.set_ylabel('Euro')
        # ax1.set_title('Finanzübersicht')

        # # Zweite Achse (ax2) für die Verluste, geteilt mit ax1
        # ax2 = ax1.twinx()
        # verluste = ergebnisse['Gesamt_Verluste']
        # ax2.bar('GesamtVerluste', verluste, color='blue')
        # ax2.set_ylabel('Verluste [Wh]', color='blue')

        # # Stellt sicher, dass die Achsenbeschriftungen der zweiten Achse in der gleichen Farbe angezeigt werden
        # ax2.tick_params(axis='y', labelcolor='blue')

        # pdf.savefig()  # Speichert den aktuellen Figure-State im PDF
        # plt.close()  # Schließt die aktuelle Figure, um Speicher freizugeben

        
        # plt.title('Gesamtkosten')
        # plt.ylabel('Euro')


        # plt.legend()
        # plt.grid(True)

        # plt.tight_layout()
        #plt.show()

    

