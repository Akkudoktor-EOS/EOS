class Wechselrichter:
    def __init__(self, max_leistung_wh, akku):
        self.max_leistung_wh = max_leistung_wh  # Maximale Leistung, die der Wechselrichter verarbeiten kann
        self.akku = akku  # Verbindung zu einem Akku-Objekt

    def energie_verarbeiten(self, erzeugung, verbrauch, hour):
        verluste = 0
        netzeinspeisung = 0
        netzbezug = 0.0
        eigenverbrauch = 0.0
        #eigenverbrauch = min(erzeugung, verbrauch)  # Direkt verbrauchte Energie

        if erzeugung > verbrauch:
            if verbrauch > self.max_leistung_wh:

                verluste += erzeugung - self.max_leistung_wh
                restleistung_nach_verbrauch = self.max_leistung_wh - verbrauch                
                netzbezug = -restleistung_nach_verbrauch
                eigenverbrauch = self.max_leistung_wh
                
            else: 
                # if hour==10:
                    # print("PV:",erzeugung)
                    # print("Load:",verbrauch)
                    # print("Max Leist:",self.max_leistung_wh)
                # PV > WR Leistung dann Verlust
                
                # Load
                restleistung_nach_verbrauch = erzeugung-verbrauch #min(self.max_leistung_wh - verbrauch, erzeugung-verbrauch)
                # Akku
                geladene_energie, verluste_laden_akku = self.akku.energie_laden(restleistung_nach_verbrauch, hour)
                rest_überschuss = restleistung_nach_verbrauch - geladene_energie
                # if hour == 12:
                    # print("Erzeugung:",erzeugung)
                    # print("Last:",verbrauch)
                    # print("Akku:",geladene_energie)
                    # print("Akku:",self.akku.ladezustand_in_prozent())
                    # print("RestÜberschuss"," - ",rest_überschuss)
                    # print("RestLesitung WR:",self.max_leistung_wh - verbrauch)
                # Einspeisung, restliche WR Kapazität
                if rest_überschuss > self.max_leistung_wh - verbrauch:
                    netzeinspeisung = self.max_leistung_wh - verbrauch
                    verluste += rest_überschuss - netzeinspeisung
                else:
                    netzeinspeisung = rest_überschuss
                
                verluste += verluste_laden_akku

                
                
                eigenverbrauch = verbrauch
 
        else:
            benötigte_energie = verbrauch - erzeugung
            max_akku_leistung = self.akku.max_ladeleistung_w
            
            rest_ac_leistung = max(max_akku_leistung - erzeugung,0)
            
            if benötigte_energie < rest_ac_leistung:
                aus_akku, akku_entladeverluste = self.akku.energie_abgeben(benötigte_energie, hour)
            else: 
                aus_akku, akku_entladeverluste = self.akku.energie_abgeben(rest_ac_leistung, hour)
            
            
            verluste += akku_entladeverluste

            netzbezug = benötigte_energie - aus_akku
            eigenverbrauch = erzeugung + aus_akku


        # if erzeugung > verbrauch:
            # if verbrauch > self.max_leistung_wh:
                
            # else: 
            
            
                # überschuss = self.max_leistung_wh - verbrauch
                
                # geladene_energie, verluste_laden_akku = self.akku.energie_laden(überschuss, hour)
                # rest_überschuss = überschuss - geladene_energie
                # verluste += verluste_laden_akku
                
                # if (rest_überschuss > self.max_leistung_wh):
                    # netzeinspeisung  = self.max_leistung_wh
                    # verluste += rest_überschuss - self.max_leistung_wh
                # else:
                    # netzeinspeisung = rest_überschuss
                
                # eigenverbrauch = verbrauch
 
        # else:
            # benötigte_energie = verbrauch - erzeugung
            # max_akku_leistung = self.akku.max_ladeleistung_w
            
            # rest_ac_leistung = max(max_akku_leistung - erzeugung,0)
            
            # if benötigte_energie < rest_ac_leistung:
                # aus_akku, akku_entladeverluste = self.akku.energie_abgeben(benötigte_energie, hour)
            # else: 
                # aus_akku, akku_entladeverluste = self.akku.energie_abgeben(rest_ac_leistung, hour)
            
            
            # verluste += akku_entladeverluste

            # netzbezug = benötigte_energie - aus_akku
            # eigenverbrauch = erzeugung + aus_akku

            # # Berechnung der gesamten verarbeiteten Energie
            # total_verarbeitet = eigenverbrauch
            # if total_verarbeitet > self.max_leistung_wh:
                # verluste += total_verarbeitet - self.max_leistung_wh

        return netzeinspeisung, netzbezug, verluste, eigenverbrauch


    # def energie_verarbeiten(self, erzeugung, verbrauch, hour):
    
        # verluste = 0
        # netzeinspeisung = 0
        # netzbezug = 0.0
        # eigenverbrauch = 0.0
        
        # if erzeugung > verbrauch:
            # überschuss = erzeugung - verbrauch
            # geladene_energie, verluste_laden_akku = self.akku.energie_laden(überschuss, hour)
            # verluste += verluste_laden_akku

            # netzeinspeisung = überschuss - geladene_energie-verluste_laden_akku
            # eigenverbrauch = verbrauch
            # netzbezug = 0.0
        # # Noch Netzbezug nötig
        # else:
            # netzeinspeisung = 0.0
            # benötigte_energie = verbrauch - erzeugung
            # aus_akku, akku_entladeverluste = self.akku.energie_abgeben(benötigte_energie, hour)
            # verluste += akku_entladeverluste
            # netzbezug = benötigte_energie - aus_akku
            # eigenverbrauch = erzeugung+aus_akku

        # return netzeinspeisung, netzbezug, verluste, eigenverbrauch  # Keine Einspeisung, Netzbezug, aus Akku, Verluste, Eigenverbrauch
