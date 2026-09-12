const BASE_URL = "http://192.168.1.151:8503";
const PREDICTION_HOURS = 72;
const SLOT_MINUTES = 15;
const now = new Date();
const start = new Date(now);
start.setHours(0, 0, 0, 0);
const slotStart = new Date(now);
slotStart.setSeconds(0, 0);
slotStart.setMinutes(Math.floor(slotStart.getMinutes() / SLOT_MINUTES) * SLOT_MINUTES);
const end = new Date(slotStart.getTime() + PREDICTION_HOURS * 3600000);

function iso(date) {
    const pad = n => String(Math.trunc(Math.abs(n))).padStart(2, "0");
    const offset = -date.getTimezoneOffset();
    const sign = offset >= 0 ? "+" : "-";
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
        `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
        `${sign}${pad(offset / 60)}:${pad(offset % 60)}`;
}

// /list interpolates über das echte Forecast-Ende hinaus. /series liefert nur
// die tatsächlich vom Provider vorhandenen Zeitpunkte.
msg.method = "GET";
msg.url = `${BASE_URL}/v1/prediction/series?key=pvforecast_ac_power` +
    `&start_datetime=${encodeURIComponent(iso(start))}` +
    `&end_datetime=${encodeURIComponent(iso(end))}`;
return msg;
