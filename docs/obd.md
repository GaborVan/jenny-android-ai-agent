# OBD-II car diagnostics

Jenny's optional `obd` tool reads standard emissions-related OBD-II diagnostics
through ELM327-compatible adapters. It is enabled by default — with no adapter
connected every action answers with a clear error instead of touching hardware,
so there is nothing to switch on before the first use. Set `tools.obd.enable` to
`false` in Jenny's configuration to hide it entirely (programmatic changes must
use `jenny.config.store.mutate`). `tools.obd.idleTimeoutS` defaults to 300 seconds.
The tool maintains one in-memory connection, serializes requests, and closes
idle or failed connections. Gateway restart requires reconnecting.

## Adapters and permissions

On Android, use a **Bluetooth Classic SPP** ELM327 adapter, paired first in
Android's Bluetooth settings. BLE-only and Wi-Fi adapters are not supported.
Connect by its paired name or MAC address; use the MAC if names are duplicated.
The vehicle ignition must be on and the adapter powered by the diagnostic port.

Android 12+ requires the runtime **Nearby devices** permission, including
`BLUETOOTH_CONNECT`. `BLUETOOTH_SCAN` is also required because Jenny cancels
Bluetooth discovery before opening RFCOMM. The manifest declares both; grant
Nearby devices under Android Settings → Apps → Jenny → Permissions before
connecting. The tool does not open a permission dialog. Earlier Android versions
use the legacy `BLUETOOTH` and `BLUETOOTH_ADMIN` permissions.
See the [Android Bluetooth permissions documentation](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions).

USB/serial ELM327 adapters are supported by the Python transport for desktop
bench diagnostics and testing, with optional `pyserial` installed separately.
The default baud rate is 38400; `SerialTransport` accepts a different baud rate.
There is no new project dependency and no Android USB-host driver. Jenny's app
runtime remains Android-only; desktop serial support is a library/test facility.
Outside Chaquopy the Bluetooth transport reports an explicit error.

## Tool actions

Example arguments for the `obd` tool:

| Action | Arguments | Result |
| --- | --- | --- |
| Status | `{"action":"status"}` | Cached session state; no adapter I/O |
| Connect | `{"action":"connect","device":"OBDII"}` | Connect and initialize ELM327 |
| Read codes | `{"action":"read_codes"}` | Stored (03), pending (07), permanent (0A) codes |
| Live snapshot | `{"action":"live","pids":["0C","0D","05"]}` | RPM, km/h, coolant °C |
| VIN | `{"action":"vin"}` | Mode 09 PID 02 vehicle identification |
| Readiness | `{"action":"readiness"}` | MIL, stored count, supported/completed monitors |
| Clear codes | `{"action":"clear_codes","confirm":true}` | Mode 04, only with explicit consent |

Clearing codes **also resets emissions readiness monitors**. Save the codes
before clearing. A successful clear does not repair a fault; permanent codes may
remain until the vehicle's own checks verify the repair. An absent or false
`confirm` is refused without sending a clear command.

Live PIDs include 0C RPM, 0D speed (km/h), 05 coolant (°C), 0F intake air (°C),
10 mass airflow (g/s), 11 throttle (%), 2F fuel level (%), 42 module voltage (V),
and 1C OBD standard. Unknown PIDs return raw byte arrays. Each PID returns a list
because multiple ECUs may respond; headers are off, so ECU identity is not exposed.
`NO DATA` yields empty lists (or a null VIN), which means no data was returned,
not proof that the vehicle has no faults or has completed all monitors.

## Scope and limitations

Only standard OBD-II is supported. Manufacturer-specific modules, ABS, airbags,
body controllers, coding, actuator tests, and proprietary fault descriptions are
outside scope. PID availability varies by vehicle. DTCs are identifiers, not a
mechanical diagnosis. VIN parsing supports ELM327 numbered CAN responses and
legacy numbered Mode 09 records; ambiguous multiple VIN replies are rejected.
The adapter uses reset defaults (headers off, CAN automatic formatting on).
Malformed replies fail explicitly rather than being presented as clean results.

The automated tests use scripted transports and a simulated Java bridge. Real
hardware validation still needs to cover Android permission grants/denials,
Bluetooth pairing and reconnection, timeout/socket abort behavior under Chaquopy,
adapter clone compatibility, ignition-off/link loss, CAN and older vehicle
protocols, and USB serial baud rates. Test clearing codes only on a vehicle where
the owner has explicitly authorized erasing codes and readiness state.
