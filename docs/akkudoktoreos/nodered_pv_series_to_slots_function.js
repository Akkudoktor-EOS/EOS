const SLOT_MINUTES = 15;
const PREDICTION_HOURS = 72;
const SLOT_MS = SLOT_MINUTES * 60000;
const raw = msg.payload && msg.payload.data;
if (!raw || typeof raw !== "object") {
    node.warn("PV-Rohprognose fehlt oder hat kein data-Objekt.");
    return null;
}

const points = Object.entries(raw)
    .map(([timestamp, value]) => [new Date(timestamp).getTime(), Number(value)])
    .filter(([timestamp, value]) => Number.isFinite(timestamp) && Number.isFinite(value))
    .sort((a, b) => a[0] - b[0]);
if (points.length < 2) {
    node.warn("PV-Rohprognose enthält weniger als zwei gültige Punkte.");
    return null;
}

const now = new Date();
const start = new Date(now);
start.setHours(0, 0, 0, 0);
const slotStart = new Date(now);
slotStart.setSeconds(0, 0);
slotStart.setMinutes(Math.floor(slotStart.getMinutes() / SLOT_MINUTES) * SLOT_MINUTES);
const endMs = slotStart.getTime() + PREDICTION_HOURS * 3600000;
const values = [];
let right = 1;
for (let timestamp = start.getTime(); timestamp < endMs; timestamp += SLOT_MS) {
    if (timestamp < points[0][0]) {
        values.push(0);
        continue;
    }
    if (timestamp > points[points.length - 1][0]) {
        // Absichtlich null: Function 3 und EOS verkürzen damit den Tail, statt
        // einen erfundenen linearen PV-Rest als echten Forecast zu behandeln.
        values.push(null);
        continue;
    }
    while (right < points.length && points[right][0] < timestamp) right++;
    const b = points[Math.min(right, points.length - 1)];
    const a = points[Math.max(right - 1, 0)];
    if (timestamp === b[0] || a[0] === b[0]) {
        values.push(b[1]);
    } else {
        const fraction = (timestamp - a[0]) / (b[0] - a[0]);
        values.push(a[1] + fraction * (b[1] - a[1]));
    }
}

msg.topic = "pv_forecast";
msg.payload = values;
node.status({
    fill: points[points.length - 1][0] >= endMs - SLOT_MS ? "green" : "yellow",
    shape: "dot",
    text: `PV echt bis ${new Date(points[points.length - 1][0]).toLocaleString()}`
});
return msg;
