import json
from datetime import datetime, timedelta, timezone
import numpy as np
from pprint import pprint

class EAuto:
    def __init__(self, soc=None, capacity = None, power_charge = None, load_allowed = None):
        self.soc = soc
        self.init_soc = soc
        self.akku_kapazitaet = capacity
        self.ladegeschwindigkeit = power_charge
        self.laden_moeglich = None
        self.stuendlicher_soc = []
        self.stuendliche_last = []  # Hinzugefügt für die Speicherung der stündlichen Last
        self.laden_moeglich = load_allowed
        self.berechne_ladevorgang()

    def reset(self):
        self.soc = self.init_soc
        self.stuendlicher_soc = []
        
    def set_laden_moeglich(self, laden_moeglich):
        self.laden_moeglich = laden_moeglich
        self.stuendlicher_soc = []  # Beginnt mit dem aktuellen SoC
        self.stuendliche_last = []  # Zurücksetzen der stündlichen Last

    def berechne_ladevorgang(self):
        if self.laden_moeglich is None:
            print("Lademöglichkeit wurde nicht gesetzt.")
            return
        
        for moeglich in self.laden_moeglich:
            if moeglich > 0.0 and self.soc < 100:
                # Berechnung der geladenen Energie basierend auf dem Anteil der Lademöglichkeit
                geladene_energie = min(self.ladegeschwindigkeit * moeglich, (100 - self.soc) / 100 * self.akku_kapazitaet)
                self.soc += geladene_energie / self.akku_kapazitaet * 100
                self.soc = min(100, self.soc)
                self.stuendliche_last.append(geladene_energie)
            else:
                self.stuendliche_last.append(0)  # Keine Ladung in dieser Stunde
            self.stuendlicher_soc.append(self.soc)
            
        # Umwandlung der stündlichen Last in ein NumPy-Array
        self.stuendliche_last = np.array(self.stuendliche_last)

    # def berechne_ladevorgang(self):
        # if self.laden_moeglich is None:
            # print("Lademöglichkeit wurde nicht gesetzt.")
            # return
        
        # for moeglich in self.laden_moeglich:
            # if moeglich > 1 and self.soc < 100:
                # geladene_energie = min(self.ladegeschwindigkeit, (100 - self.soc) / 100 * self.akku_kapazitaet)
                # self.soc += geladene_energie / self.akku_kapazitaet * 100
                # self.soc = min(100, self.soc)
                # self.stuendliche_last.append(geladene_energie)
            # else:
                # self.stuendliche_last.append(0)  # Keine Ladung in dieser Stunde
            # self.stuendlicher_soc.append(self.soc)
            
        # # Umwandlung der stündlichen Last in ein NumPy-Array
        # self.stuendliche_last = np.array(self.stuendliche_last)

    def get_stuendliche_last(self):
        """Gibt das NumPy-Array mit der stündlichen Last zurück."""
        return self.stuendliche_last

    def get_stuendlicher_soc(self):
        """Gibt den stündlichen SoC als Liste zurück."""
        return self.stuendlicher_soc


# class EAuto:
    # def __init__(self, soc, akku_kapazitaet, ladegeschwindigkeit):
        # self.soc = soc
        # self.akku_kapazitaet = akku_kapazitaet
        # self.ladegeschwindigkeit = ladegeschwindigkeit
        # self.laden_moeglich = None
        # # Initialisieren des Arrays für den stündlichen SoC
        # self.stuendlicher_soc = []

    # def set_laden_moeglich(self, laden_moeglich):
        # """
        # Setzt das Array, das angibt, wann das Laden möglich ist.
        # :param laden_moeglich: Ein Array von 0 und 1, das die Lademöglichkeit angibt
        # """
        # self.laden_moeglich = laden_moeglich
        # # Zurücksetzen des stündlichen SoC Arrays bei jeder neuen Lademöglichkeit
        # self.stuendlicher_soc = [self.soc]  # Start-SoC hinzufügen

    # def berechne_ladevorgang(self):
        # """
        # Berechnet den Ladevorgang basierend auf der Ladegeschwindigkeit und der Lademöglichkeit.
        # Aktualisiert den SoC entsprechend und speichert den stündlichen SoC.
        # """
        # if self.laden_moeglich is None:
            # print("Lademöglichkeit wurde nicht gesetzt.")
            # return
        
        # for i, moeglich in enumerate(self.laden_moeglich):
            # if moeglich == 1:
                # # Berechnen, wie viel Energie in einer Stunde geladen werden kann
                # geladene_energie = min(self.ladegeschwindigkeit, (100 - self.soc) / 100 * self.akku_kapazitaet)
                # # Aktualisieren des SoC
                # self.soc += geladene_energie / self.akku_kapazitaet * 100
                # # Sicherstellen, dass der SoC nicht über 100% geht
                # self.soc = min(100, self.soc)
                # print(f"Stunde {i}: Geladen {geladene_energie} kWh, neuer SoC: {self.soc}%")
            # else:
                # # Wenn nicht geladen wird, bleibt der SoC gleich
                # print(f"Stunde {i}: Nicht geladen, SoC bleibt bei {self.soc}%")
            # self.stuendlicher_soc.append(self.soc)  # Aktuellen SoC zum Array hinzufügen
            # if self.soc >= 100:
                # print("Akku vollständig geladen.")
                # break

    # def berechne_benoetigte_energie(self):
        # """
        # Berechnet die Gesamtenergie, die benötigt wird, um den Akku vollständig zu laden.
        # """
        # return (100 - self.soc) / 100 * self.akku_kapazitaet

    # def get_stuendlicher_soc(self):
        # """
        # Gibt den stündlichen Ladezustand (SoC) als Array zurück.
        # """
        # return self.stuendlicher_soc


if __name__ == "__main__":
    # Initialisierung des Elektroauto-Ladens
    mein_eauto = EAuto(soc=50, akku_kapazitaet=60, ladegeschwindigkeit=11)
    
    # Festlegen, wann das Laden möglich ist (1 = Laden erlaubt, 0 = Laden nicht erlaubt)
    # Beispiel: Laden ist nur während der Nachtstunden und frühen Morgenstunden erlaubt
    laden_moeglich = [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1]
    mein_eauto.set_laden_moeglich(laden_moeglich)
    
    # Durchführen des Ladevorgangs
    mein_eauto.berechne_ladevorgang()
    
    # Abrufen und Ausgeben des stündlichen Ladezustands
    stuendlicher_soc = mein_eauto.get_stuendlicher_soc()
    print("\nStündlicher SoC während des Ladevorgangs:")
    for stunde, soc in enumerate(stuendlicher_soc):
        print(f"Stunde {stunde}: SoC = {soc}%")
