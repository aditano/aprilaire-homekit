# Research

AprilAire Home is a local desktop app: it speaks AprilAire's automation socket on the LAN and advertises a HomeKit thermostat from the same computer. It does not use the AprilAire cloud, a VPS, or a paid relay.

That choice is forced by what the 8920W actually exposes. There is a local automation protocol, documented and implemented in the open, and there is no public cloud API for a third-party HomeKit bridge. The parts that are still unproven are called out at the end. They have to be checked on the Woodland Rd panel.

## Decision

| Option | Why it lost or won |
| --- | --- |
| AprilAire cloud account, then a bridge | No documented third-party cloud API. The phone app and Alexa/Google use AprilAire's servers. Building on that would be an unofficial login, and it would still need a computer for HomeKit. |
| Homebridge / Node plugin | No AprilAire Homebridge plugin exists. It would be a second always-on service plus a JSON config, which is the opposite of a one-window setup. |
| Matter | The 8920W is not a Matter thermostat. Apple cannot discover it natively. |
| HAP-python on this computer | This is the path. `pyaprilaire` already speaks the automation socket, including model id 6 (8920W). HAP-python already implements a thermostat accessory, pairing, QR setup URI, and persistent keys. The window is a local page in the OS web view, the same idea as Tauri, so we do not ship a second copy of Chromium. |
| VPS, ngrok, or a cloud relay | Rejected. The thermostat and the iPhone are on the same home network. A relay would add a failure point and would send home data off the LAN. |

The computer in the house is the bridge. HomeKit pairing is local. The setup code and accessory keys never leave that machine.

## The 8920W and the automation socket

AprilAire's Wi-Fi thermostats can talk to an automation system over a raw TCP socket. The open implementation is [pyaprilaire](https://github.com/chamberlain2007/pyaprilaire) 0.8.1, which Home Assistant uses. The protocol notes that match the code and the public automation description:

- 8800-series panels, which is the family the 8920W belongs to, listen on **TCP 8000**. 6000-series panels use **TCP 7000**. This app probes both, then keeps the port that answers the identification read.
- There is **no password**. Security is "this device is on your LAN."
- The panel allows **one** automation client. A second connection can lock the panel until power is removed. Home Assistant's integration treats that as a hard rule, and so does this app.
- Frames are `revision, sequence (0–127), length (16-bit), action, functional domain, attribute, payload, CRC-8`. The CRC is poly `0x31`, init `0`, no reflection, no final xor. `pyaprilaire` uses the `crc` package with that configuration.
- Actions include read request, read response, write, change-of-state (COS), and NACK. Domains include setup, control, scheduling, sensors, status, and identification.
- Model numbers in the library include `6: 8920W` (and siblings 8810, 8820, 8830, 8840, 8910W, and others). A successful MAC read is enough to know the peer speaks this protocol. The model byte says whether it is an 8920W.
- Temperatures are a 6-bit magnitude, a half-degree flag, and a sign bit. That cannot hold a typical indoor Fahrenheit number, and Home Assistant treats the decoded value as Celsius. This app does the same and converts only for display. Setpoints move in **0.5°C** steps. A write of `0` means "do not change this setpoint," which is how a heat-only or cool-only change is sent.
- Modes on the wire are `1` off, `2` heat, `3` cool, `4` emergency heat, `5` auto. HomeKit has no emergency-heat target, so emergency heat is presented as Heat.
- The library's author reconnects about once an hour because COS updates can go quiet. This app does that, and it also re-reads sensors, control, and equipment status every 60 seconds. Retries back off from 5 seconds to 60.
- Commands are queued and sent about twice a second (`QUEUE_FREQUENCY` is 0.5s in pyaprilaire 0.8.1). There is no separate published rate limit beyond that and the single-client rule.

Home Assistant's climate entity is the best public description of day-to-day behavior: <https://www.home-assistant.io/integrations/aprilaire/> and the component in `homeassistant/components/aprilaire`. It supports the 8810, 8820, 8830, 8840, 8910, and 8920. It polls by holding the one socket, not by opening a new connection per update.

### Automation Enable, and the 8920W manual

8800-series installer manuals that document local automation (8810, 8820, 8830) tell the installer to open the contractor menu and set **00. Automation Enable** to **1: Automation System**. `0` leaves the panel in Aprilaire Cloud mode, which is what Alexa and Google use, and the automation port stays closed.

The 8920W installation manual that AprilAire publishes for that model does **not** list setting 00. The protocol table still has a 8920W model id, and the Home Assistant integration lists the 8920. Owners have also reported the port closed, the menu missing, or the panel locking up when something else already held the socket.

So the honest state is:

- If the contractor menu has Automation Enable, set it to Automation System. The app can then discover and control the panel. Expect the cloud voice assistants to be affected, because the manuals that mention the setting describe the two modes as alternatives. The AprilAire phone app has been reported to keep working, because it does not use this socket. Confirm that on site.
- If the menu is not there, this app cannot invent a local session. There is no documented cloud API to fall back on, so the window explains that instead of asking for an AprilAire password.

Discovery is not mDNS. Nothing in the public protocol says the panel advertises itself. The app takes each real IPv4 interface, narrows anything wider than a /24 down to the local /24, skips obvious virtual interfaces, and tries TCP 8000 and 7000. Hosts that accept a connection get one identification session (MAC, and model when the panel sends it). The socket is closed before the next host is probed, so the scan itself does not hold the single client slot. A manual IP address uses the same identification read.

## HomeKit

Third-party accessories speak the HomeKit Accessory Protocol on the LAN. This app uses [HAP-python](https://github.com/ikalchev/HAP-python) (4.9 or 5.x).

- Category is thermostat.
- The thermostat service exposes current temperature, target temperature, target mode (Off / Heat / Cool / Auto), current mode (Off / Heat / Cool, meaning idle / heating / cooling), display units, current humidity, and the auto-mode heat and cool thresholds.
- A separate humidity sensor service carries the same humidity reading, which is what the Home app expects when it shows a humidity line.
- Temperatures on HomeKit's wire are Celsius. Display units are a characteristic (`0` Celsius, `1` Fahrenheit).
- HomeKit's heating threshold characteristic tops out at 25°C (77°F). A heat setpoint above that is clamped on the threshold characteristic. The single target temperature, used for Heat and Cool, can go higher (the characteristic allows up to 38°C).
- Pairing is the standard setup-code flow. HAP-python builds an `X-HM://` URI from the category, the flags, the 8-digit code, and a 4-character setup id. The window draws that URI as a QR. Apple rejects trivial codes (`000-00-000`, `111-11-111`, `123-45-678`, `876-54-321`); the app will not generate those.
- Keys, the accessory MAC, and paired clients are stored in `homekit.state`. The setup code and setup id are stored in `homekit.json`. Both stay in the per-user data directory. Resetting pairing deletes `homekit.state` so the old Home pairing cannot keep working. The phone must forget the accessory first.
- The accessory is advertised with mDNS. The iPhone and this computer must be on a network that allows client-to-client traffic and multicast. A guest Wi-Fi that isolates clients will not pair.
- HAP-python's driver owns one asyncio loop. This app runs that loop on a background thread so the window can start and stop it. Stopping calls `async_stop`, which unregisters mDNS and closes the HomeKit server. Starting again creates a new driver and loads the same state file, so paired phones stay paired.

The window's HTTP API is bound to `127.0.0.1` only. It does not send CORS headers, and `/api/` requires a custom header so a random website cannot read the setup code with a browser request.

## What already existed

- **pyaprilaire** and the Home Assistant AprilAire integration are the working local client. This app vendors neither of them; it depends on `pyaprilaire==0.8.1` and wraps the client so a dead socket cannot leak its queue task or stick the library's reconnect flag.
- **HAP-python** is the HomeKit side. Home Assistant's HomeKit bridge uses the same library, which is why the pairing and thermostat behavior match what people already run at home.
- **Homebridge** has no AprilAire thermostat plugin worth building on. A search of the usual plugin lists does not turn up a maintained 8920W bridge.
- **Matter** is not an option for this hardware.
- AprilAire's own app, Alexa, and Google are cloud features of the panel. They are not a HomeKit API.

## Desktop app

The owner asked for a window, not a terminal, and for the thermostat to be found automatically.

- The bridge process serves the page and the API on `127.0.0.1`.
- Opening the app starts that process if it is not already running, then opens a native web view (`pywebview`: WebView2 on Windows, WebKit on macOS, WebKitGTK on Linux). If the toolkit is missing, the same page opens in the browser. The bridge keeps running after the window closes.
- Login start is a user service: systemd `--user` on Linux, a LaunchAgent on macOS, and a Task Scheduler ONLOGON task on Windows. The toggle in the window writes that item. The service runs `aprilaire-homekit --no-window`. It does not start a second copy if one is already listening; the new process exits 0 so the service manager does not restart-loop.
- The scan, the manual IP check, the pairing QR, start/stop, the login toggle, and reset pairing are all in the window. A `check` command exists for support and is not the normal path.

## What has to be confirmed on the 8920W

These were not run against the physical thermostat:

1. Contractor Info on that unit shows Automation Enable, and setting it to Automation System opens TCP 8000.
2. An identification read returns model 6 (8920W) and a MAC address.
3. Mode and setpoint writes change the equipment, and sensor reads match the screen.
4. The iPhone pairs from the QR and the tile updates.
5. The AprilAire phone app, and Alexa or Google if they are used, still behave acceptably after leaving cloud mode.
6. Leaving the socket up for a day does not freeze the panel. The hourly reconnect is there because the library author needed it, not because we measured this panel.

Until those are true on site, the app is a faithful client of the published protocol, not a claim that every 8920W firmware build exposes it.

## Sources

- pyaprilaire, including the model table and the CRC/framing implementation: <https://github.com/chamberlain2007/pyaprilaire>
- Home Assistant AprilAire integration: <https://www.home-assistant.io/integrations/aprilaire/>
- HAP-python: <https://github.com/ikalchev/HAP-python>
- HomeKit thermostat characteristics (current and target temperature, heating/cooling state, display units, humidity): Apple's HomeKit Accessory Protocol characteristic definitions, as implemented by HAP-python's thermostat service.
- AprilAire 8800-series automation: installer manuals for the 8810/8820/8830 describe Automation Enable and the local socket; the 8920W installation manual does not list that setting. The 8920W model id is still present in pyaprilaire.
