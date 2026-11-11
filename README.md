# myair3-webcontrol

A small local web app to control an Advantage Air MyAir3 (Series 3) ducted air conditioner from a browser, after the MyAir3 iOS app stopped finding the system on my home network.

## Why this exists

The wall panel and the air conditioner still worked. The iOS app used to work too, then it stopped finding the system. I tried the usual things without success: iOS Local Network permission on, VPN off, reinstalling the app, and the router Wi-Fi and multicast settings. The controller itself answered fine from Safari over its plain HTTP API, so the controller was still reachable and answering requests.

I built this instead. It is a single Python file that talks to the controller directly, plus a mobile-friendly page modelled on the layout of the original app.

The most likely cause of the original problem: the iOS app appears not to have been updated for newer iOS network rules. The app finds the controller with a UDP broadcast. Since iOS 14, Apple requires apps that send broadcast or multicast traffic to hold the `com.apple.developer.networking.multicast` entitlement, and an older app without that entitlement can no longer send the discovery packet. I did not inspect the iOS app. My analysis is based on the official Android app, `MyAir3 – 3.290 [APK]`, downloaded from https://www.advantageair.com.au/apps/ . I also did not fully rule out a router issue, see Checking discovery yourself below.

## How the original app finds the controller

These details come from the Android app 3.290 mentioned above.

1. The app sends the ASCII text `identify` by UDP, from source port 3000 to port 3000 at the broadcast address of the phone subnet, roughly every 0.8 seconds.
2. It builds that broadcast address by assuming a /24 network and replacing the last octet of the phone IP address with 255, so `192.168.1.20` becomes `192.168.1.255`.
3. It listens on UDP port 3001 for a reply.
4. The controller replies with XML that starts with `<iZS10.3><system>` and ends with `</iZS10.3>`. The reply contains `<mac>`, `<name>`, `<type>`, and `<ip>`.
5. After discovery, the app logs in over HTTP and loads the system data. The local IP address is not saved, and there is no way to enter an IP address by hand, so if discovery fails the app cannot connect at all.

The `<type>` values seen in the app code are:

| Type | System |
| --- | --- |
| 9 | MyAir2 |
| 10 | Zone10 |
| 12 | MyAir3 |
| 13 | e-zone |
| 14 | MyAir4 |
| 15 | e-zone i |
| 16 | MyAir4i |
| 17 | MyAir5 |
| 18 | Zone10e |

This project is only tested with type 12, MyAir3.

### Checking discovery yourself

This short script does the same thing the app does. It sends `identify` and prints any reply it receives. Replace `192.168.1.255` with the broadcast address of your own network first.

```python
import socket, select, time
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
tx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
tx.bind(("", 3000))
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
rx.bind(("", 3001))
tx.sendto(b"identify", ("192.168.1.255", 3000))
end = time.time() + 3
while time.time() < end:
    for s in select.select([rx, tx], [], [], 0.3)[0]:
        data, addr = s.recvfrom(4096)
        if data != b"identify":
            print(addr, data[:400])
```

If a wired machine gets a reply and a machine on Wi-Fi does not, your router is most likely filtering the broadcast between the two. That is worth checking before you blame the app or the controller.

## Controller API

The controller speaks plain HTTP on port 80 and answers with XML. After a successful login the answers contain `<authenticated>1</authenticated>`.

| Request | What it does |
| --- | --- |
| `login?password=password` | Log in to the controller |
| `getSystemData` | Read the current system state |
| `getZoneData?zone=N` | Read one zone |
| `setSystemData?airconOnOff=0\|1` | Turn the air conditioner off or on |
| `setSystemData?mode=1\|2\|3\|5` | Set mode: 1 cool, 2 heat, 3 fan, 5 dry |
| `setSystemData?fanSpeed=1\|2\|3\|4` | Set fan speed: 1 low, 2 medium, 3 high, 4 auto |
| `setSystemData?centralDesiredTemp=N` | Set target temperature, between `minUserTemp` and `maxUserTemp`, usually 16 to 32 |
| `setZoneData?zone=N&zoneSetting=0\|1` | Close or open a zone |
| `setZoneData?zone=N&userPercentSetting=P` | Set zone airflow, 5 to 100 in steps of 5 |

You do not need to use this API directly. The web app wraps it, validates the values, and logs in again on its own when the controller asks for it.

## Features

The repo contains:

- `myair3_web.py`, a single Python 3 file using only the standard library
- `index.html` and a `static/` folder with original SVG icons
- An example systemd user unit, `myair3-web.service`

The page gives you air-con on and off, mode, target temperature, and fan speed. Below that is the zone list. Tap a zone to open or close it, and use the minus and plus buttons to change its airflow percent.

The browser does not talk to the controller directly. It talks to a small JSON API in `myair3_web.py`:

| Request | What it does |
| --- | --- |
| `GET /api/state` | Read the current state |
| `POST /api/system` | Change a system setting |
| `POST /api/zone` | Change a zone setting |

The server checks every value before it forwards anything, and it sends one request at a time to the controller. The POST endpoints only accept `application/json`.

## Quick start

You need Python 3 and the IP address of your controller, for example `192.168.1.50`.

```bash
MYAIR3_HOST=192.168.1.50 MYAIR3_ALLOW=192.168.1.0/24 python3 myair3_web.py
```

Then open `http://<server-ip>:8088` in a browser on the same network, with the IP address or hostname of the machine running the script.

It helps to give both the controller and the server a DHCP reservation in your router, so their addresses do not change later.

## Run at boot

As a systemd user service:

1. Copy `myair3-web.service` to `~/.config/systemd/user/`
2. Edit the `Environment=` lines and the `ExecStart` path in your copy
3. Start it with:

```bash
systemctl --user enable --now myair3-web
loginctl enable-linger $USER
```

The second command lets the service start at boot without you having to log in first.

## Add to iPhone home screen

Open the page in Safari, tap Share, then Add to Home Screen. You get a full-screen icon that behaves like an app, just without the discovery problem.

## Configuration

Everything is set with environment variables.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MYAIR3_HOST` | `192.168.1.50` | IP address of the controller |
| `MYAIR3_BIND` | `0.0.0.0` | Address the web server listens on |
| `MYAIR3_PORT` | `8088` | Port the web server listens on |
| `MYAIR3_ALLOW` | `192.168.1.0/24,127.0.0.0/8` | Comma-separated list of networks allowed to use the page |

## Security

Access is restricted by client IP address to the networks in `MYAIR3_ALLOW`. There is no login on the web app itself. That is a deliberate choice for a small tool on a home network, and it has a clear consequence: keep it on your LAN and never port-forward it to the internet. Anyone who can reach the page can run your air conditioner.

## Compatibility

Tested with my own system: controller CB 4.54.0, TSP 4.2, MyAppRev 7.4, system type 12. It should work with other MyAir3 units. For the other system types listed above I do not know, I have none of them here to test with.

## Disclaimer

This is an independent project. It is not affiliated with, endorsed, or supported by Advantage Air. MyAir, MyAir3, and Advantage Air are trademarks of Advantage Air, used here only to describe compatibility.

No Advantage Air code, APK, or images are included in this repo. All code and graphics in it are original work.

Use it at your own risk. It controls real equipment, so check what you are changing before you rely on it.

## License

MIT. Copyright (c) 2026 Peter Taras. See the `LICENSE` file in this repo.
