import datetime
import pytz

def ist_dst_wechsel(tag, timezone="Europe/Berlin"):
    """Prüft, ob an einem gegebenen Tag die Sommerzeit beginnt oder endet."""
    tz = pytz.timezone(timezone)
    # Hole den aktuellen Tag und den nächsten Tag
    aktueller_tag = datetime.datetime(tag.year, tag.month, tag.day)
    naechster_tag = aktueller_tag + datetime.timedelta(days=1)

    # Lokalisiere die Tage in der gegebenen Zeitzone
    aktueller_tag_localized = tz.localize(aktueller_tag, is_dst=None)
    naechster_tag_localized = tz.localize(naechster_tag, is_dst=None)

    # Prüfe, ob die UTC-Offsets unterschiedlich sind (DST-Wechsel)
    dst_wechsel = aktueller_tag_localized.dst() != naechster_tag_localized.dst()

    return dst_wechsel

# # Beispielverwendung
# start_datum = datetime.datetime(2024, 3, 31)  # Datum der DST-Umstellung
# if ist_dst_wechsel(start_datum):
    # prediction_hours = 23  # Anpassung auf 23 Stunden für DST-Wechseltage
# else:
    # prediction_hours = 24  # Standardwert für Tage ohne DST-Wechsel
