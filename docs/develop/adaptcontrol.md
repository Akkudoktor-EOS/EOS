# `control` — Control Adapter

## Overview

The control adapter orchestrates and manages the device controllers. Device controllers
translate the energy management plan instructions to concrete control commands of a device and
control amd monitor this device. Control may be established by ModBus or any other means to
access a device.

## Shared data store

All device controllers share an async and thread safe data store for cross device data.

### Why This Works in Both Worlds

From a coroutine: await store.publish(...) runs publish_sync inline. The threading.Lock acquisition
is effectively instant because the critical section is tiny, so it does not starve the event loop.
If you want to be strict about "no blocking calls in the event loop," you can wrap it in
asyncio.to_thread, but that adds overhead for no benefit here — a dict merge of a few dozen keys is
nanoseconds.

From a thread: store.publish_sync(...) acquires the lock and swaps the reference. No asyncio
primitives are touched, so it is safe to call from asyncio.to_thread, from a
concurrent.futures.ThreadPoolExecutor, or from a library's callback thread.

Readers: snapshot() and get() take no lock. They read self._image, which is only ever replaced by a
reference swap under the lock. Under the GIL, a reference read or a dict(...) copy is atomic, so a
reader either sees the pre-publish image or the post-publish image, never a half-merged one.

### Using It From a Device That Reads in a Thread

A typical case: a blocking library or a to_thread wrapper. The read is offloaded to a thread, and
the publish happens on the event loop after the await — or, if the library pushes data from its own
thread, the callback uses publish_sync.

```python
class EnergyMeter(ControllerBase):

    def __init__(self, device_id, settings, shared_data, modbus):
        super().__init__(device_id, settings, shared)
        self._modbus = modbus

    async def read_input_image(self):
        # Blocking Modbus read, offloaded to a thread.
        grid_w, pv_w = await asyncio.to_thread(self._read_blocking)
        return {"grid_power_w": grid_w, "pv_power_w": pv_w,
                "timestamp": time.monotonic()}

    def _read_blocking(self) -> tuple[float, float]:
        return (
            self._modbus.read_float("grid_power"),
            self._modbus.read_float("pv_power"),
        )

    def shared_data_keys(self, input_image):
        return {
            f"{self.device_id}.grid_power_w": input_image["grid_power_w"],
            f"{self.device_id}.pv_power_w": input_image["pv_power_w"],
            f"{self.device_id}.updated_at": input_image["timestamp"],
        }
```

If the Modbus library instead pushes meter values from its own thread, that callback can publish
directly without touching the event loop:

```python
def _on_meter_push(self, grid_w: float, pv_w: float) -> None:
    # Called from the Modbus library's thread — no asyncio involved.
    self.shared_data.publish_sync({
        f"{self.device_id}.grid_power_w": grid_w,
        f"{self.device_id}.pv_power_w": pv_w,
        f"{self.device_id}.updated_at": time.monotonic(),
    })
```

The compressor's process still reads via shared_image passed in by the scan, so it never needs to
know whether the meter published from a thread or a coroutine.

### Using It From a Device That Writes From a Thread Pool

If a device's read_input_image runs in a thread pool and wants to publish inline (rather than
returning to scan), it can call publish_sync directly:

```python
async def read_input_image(self):
    async def _worker() -> dict[str, Any]:
        # This body runs in a worker thread.
        raw = self._modbus.read_block(0, 10)
        self.shared_data.publish_sync({
            f"{self.device_id}.power_w": raw[0],
            f"{self.device_id}.updated_at": time.monotonic(),
        })
        return {"power_w": raw[0], "timestamp": time.monotonic()}

    return await asyncio.to_thread(_worker)
```

### One Caveat

publish (async) and publish_sync do the same critical section, so there is no re-entrancy concern.
The only thing to avoid is calling publish_sync from inside a snapshot() iteration while also
expecting the iteration to see the update — snapshot returns a copy, so the update is visible only
to the next snapshot. That is the intended semantics.

If it is ever needed to do multi-key reads and writes in one atomic step (read-modify-write against
the store), add a dedicated method that takes the lock once:

```python
def update(self, fn) -> None:
    """Atomically apply fn to the image. fn receives a mutable copy
    and returns the new image. Runs under the lock; fn must not block."""
    with self._lock:
        new_image = fn(dict(self._image))
        self._image = new_image
```

But for the control use case, batch publish plus snapshot-once-per-scan is sufficient and avoids the
temptation to put control logic inside a lock.

## Device controller

The device controller is two split, the EOS-server side of the device controller and the thread
running the control loop for one device controller. The device controller owns the thread and
starts and stops it.

## Status REST API

The REST API `/v1/control` (`server/rest/control.py`) provides the read-only status of the device
controllers, e.g. for the Control tab of EOSdash:

| Endpoint | Content |
|---|---|
| `GET /v1/control` | Every configured controller with its state |
| `GET /v1/control/{device_id}/status` | State, active command, readings and settings |
| `GET /v1/control/{device_id}/diagnostics` | Diagnostic series and suggested graphs |
| `GET /v1/control/{device_id}/events?limit=100` | Last raw diagnostic events, newest first |

The state of a controller is `running`, `start_failed` (the last start failed, `error` holds the
start error) or `stopped` (not running, e.g. until the next start after a reconfiguration). The
control adapter keeps the start error itself (`ControlAdapter.start_errors`), so it is available
even while the diagnostic log is off.

The diagnostic series and events are only collected while the diagnostic log of the controller is
enabled (`diagnostic_log_capacity` > 0, reported as `diagnostics_enabled`). They survive controller
restarts.

What a controller provides for the status:

- Readings: the data its thread publishes (`ControlStatus.data`). Keys with a unit suffix
  (`_w`, `_kwh`, `_percent`, `_c`, `_factor`) are shown with their unit by EOSdash.
- Active command: the command dataclass. The fields of `ControlCommand` are shown as command data,
  the fields a subclass adds (e.g. `mode`, `operation_factor`) as its payload; enums are shown by
  their value.
- Graphs: the fields of its events declared in `DIAGNOSTIC_SERIES`.
