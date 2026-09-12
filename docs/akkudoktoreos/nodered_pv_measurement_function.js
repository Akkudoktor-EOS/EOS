// Node-RED function: cumulative PV production meter readings -> EOS measurement API.
//
// Strang:
//     inject (repeat 3600 s, msg.topic = die SQL aus dem Tutorial)
//       -> mysql "MariaDB" (DB `sensor`)
//       -> DIESE function
//       -> http request (Method: "- set by msg.method -")
//
// EOS speichert unter `pv_production_emr_keys` Zaehlerstaende (EMR) in kWh und
// bildet die Differenzen selbst. Aus den Momentanleistungen in `data.solarallpower`
// muss also erst ein monoton steigender Zaehler werden - das macht die SQL.
//
// Wichtig: das Startdatum in der SQL bleibt FEST. Ein rollendes
// `NOW() - INTERVAL n DAY` verschiebt den Nullpunkt der kumulativen Summe bei
// jedem Lauf, und EOS liest den Sprung als Produktion.

const BASE_URL = "http://192.168.1.151:8503";
const KEY = "pv_produktion_emr";
const TZ = "Europe/Berlin";

function toIsoWithOffset(value) {
    // DATE_FORMAT() kommt als String in lokaler Zeit zurueck. Den Offset aus dem
    // Datum selbst bilden, damit CEST und CET beide stimmen.
    const date = value instanceof Date ? value : new Date(String(value).replace(" ", "T"));
    const pad = n => String(Math.trunc(Math.abs(n))).padStart(2, "0");
    const offsetMin = -date.getTimezoneOffset();
    const sign = offsetMin >= 0 ? "+" : "-";
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
        `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
        `${sign}${pad(offsetMin / 60)}:${pad(offsetMin % 60)}`;
}

const rows = Array.isArray(msg.payload) ? msg.payload : [];
const data = {};
let last = null;
let skipped = 0;
for (const row of rows) {
    const value = Number(row.emr);
    if (!Number.isFinite(value)) {
        skipped += 1;
        continue;
    }
    // Ein Zaehler laeuft nur vorwaerts. Ein Rueckschritt bedeutet eine Luecke oder
    // einen kaputten Messwert - EOS wuerde daraus eine negative Produktionsstunde
    // machen.
    if (last !== null && value < last) {
        skipped += 1;
        continue;
    }
    last = value;
    data[toIsoWithOffset(row.ts)] = Number(value.toFixed(6));
}

const count = Object.keys(data).length;
if (count === 0) {
    node.warn("Keine PV-Messwerte gefunden - topic und Zeitfenster in der SQL pruefen.");
    return null;
}
if (skipped > 0) {
    node.warn(`${skipped} Zeilen uebersprungen (nicht-numerisch oder Zaehler rueckwaerts).`);
}
node.status({ text: `${count} EMR-Werte, letzter ${last.toFixed(1)} kWh` });

msg.method = "PUT";
msg.url = `${BASE_URL}/v1/measurement/series?key=${encodeURIComponent(KEY)}`;
msg.headers = { "Content-Type": "application/json" };
msg.payload = { data: data, dtype: "float64", tz: TZ };
return msg;
