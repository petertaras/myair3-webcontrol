#!/usr/bin/env python3
"""MyAir3 WebControl: a LAN-only web UI for an Advantage Air MyAir3 (Series 3) controller.

Talks to the controller's own HTTP/XML API (the same calls the official app
makes after discovery). Python 3 standard library only.

Copyright (c) 2026 Peter Taras. MIT License. Not affiliated with Advantage Air.

Controller API (from MyAir3 Android app 3.290):
  GET /login?password=password
  GET /getSystemData
  GET /getZoneData?zone=N
  GET /setSystemData?airconOnOff=0|1
  GET /setSystemData?mode=1|2|3|5          (cool, heat, fan, dry)
  GET /setSystemData?fanSpeed=1|2|3|4      (low, medium, high, auto)
  GET /setSystemData?centralDesiredTemp=N  (minUserTemp..maxUserTemp)
  GET /setZoneData?zone=N&zoneSetting=0|1
  GET /setZoneData?zone=N&userPercentSetting=P

Config by environment variable:
  MYAIR3_HOST     controller IP            (default 192.168.1.50)
  MYAIR3_BIND     listen address           (default 0.0.0.0)
  MYAIR3_PORT     listen port              (default 8088)
  MYAIR3_ALLOW    allowed client CIDRs     (default 192.168.1.0/24,127.0.0.0/8)
"""
import ipaddress
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.environ.get("MYAIR3_HOST", "192.168.1.50")
BIND = os.environ.get("MYAIR3_BIND", "0.0.0.0")
PORT = int(os.environ.get("MYAIR3_PORT", "8088"))
ALLOW = [ipaddress.ip_network(c.strip()) for c in
         os.environ.get("MYAIR3_ALLOW", "192.168.1.0/24,127.0.0.0/8").split(",") if c.strip()]
INDEX = (Path(__file__).parent / "index.html").read_bytes()
CTYPES = {".png": "image/png", ".svg": "image/svg+xml"}
STATIC = {p.name: (p.read_bytes(), CTYPES[p.suffix]) for p in (Path(__file__).parent / "static").iterdir()
          if p.suffix in CTYPES}

MODES = {1: "cool", 2: "heat", 3: "fan", 5: "dry"}
FANS = {1: "low", 2: "medium", 3: "high", 4: "auto"}

_lock = threading.Lock()  # the controller is old: one request at a time
_zone_cache = {"t": 0.0, "n": 0, "zones": []}


def _get(path):
    url = f"http://{HOST}/{path}"
    with urllib.request.urlopen(url, timeout=6) as r:
        return r.read().decode("utf-8", "replace")


def ctl(path):
    """Call the controller, logging in again if the session has lapsed."""
    with _lock:
        body = _get(path)
        if "<authenticated>1</authenticated>" not in body:
            _get("login?password=password")
            body = _get(path)
        if "<authenticated>1</authenticated>" not in body:
            raise RuntimeError("controller did not authenticate")
        return body


def tag(xml, name, default=None):
    m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
    return m.group(1).strip() if m else default


def num(v, cast=float, default=None):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def read_system():
    x = ctl("getSystemData")
    const = {num(tag(x, f"zsConstantZone{i}"), int) for i in (1, 2, 3)} - {0, None}
    return {
        "name": tag(x, "name"),
        "on": tag(x, "airconOnOff") == "1",
        "mode": num(tag(x, "mode"), int),
        "fan": num(tag(x, "fanSpeed"), int),
        "actualTemp": num(tag(x, "centralActualTemp")),
        "desiredTemp": num(tag(x, "centralDesiredTemp")),
        "minTemp": num(tag(x, "minUserTemp"), default=16.0),
        "maxTemp": num(tag(x, "maxUserTemp"), default=32.0),
        "error": (tag(x, "airConErrorCode") or "").strip(),
        "numZones": num(tag(x, "numberOfZones"), int, 0),
        "constantZones": sorted(const),
    }


def read_zones(n, max_age=0.0):
    if max_age and _zone_cache["n"] == n and time.time() - _zone_cache["t"] < max_age:
        return _zone_cache["zones"]
    zones = []
    for z in range(1, n + 1):
        x = ctl(f"getZoneData?zone={z}")
        zones.append({
            "zone": z,
            "name": tag(x, "name"),
            "open": tag(x, "setting") == "1",
            "percent": num(tag(x, "userPercentSetting"), int),
            "minDamper": num(tag(x, "minDamper"), int, 0),
            "maxDamper": num(tag(x, "maxDamper"), int, 100),
            "percentAvail": tag(x, "userPercentAvail") == "1",
            "actualTemp": num(tag(x, "actualTemp")),
            "lowBattery": tag(x, "hasLowBatt") == "1",
            "motorError": tag(x, "hasMotorError") == "1",
        })
    _zone_cache.update(t=time.time(), n=n, zones=zones)
    return zones


def state(max_age=0.0):
    s = read_system()
    s["zones"] = read_zones(s["numZones"], max_age)
    s["modes"] = MODES
    s["fans"] = FANS
    return s


def apply_system(req, sysinfo):
    if "on" in req:
        ctl(f"setSystemData?airconOnOff={1 if req['on'] else 0}")
    if "mode" in req:
        m = int(req["mode"])
        if m not in MODES:
            raise ValueError("bad mode")
        ctl(f"setSystemData?mode={m}")
    if "fan" in req:
        f = int(req["fan"])
        if f not in FANS:
            raise ValueError("bad fan speed")
        ctl(f"setSystemData?fanSpeed={f}")
    if "desiredTemp" in req:
        t = int(round(float(req["desiredTemp"])))
        if not sysinfo["minTemp"] <= t <= sysinfo["maxTemp"]:
            raise ValueError("temperature out of range")
        ctl(f"setSystemData?centralDesiredTemp={t}")


def apply_zone(req, sysinfo):
    z = int(req["zone"])
    if not 1 <= z <= sysinfo["numZones"]:
        raise ValueError("bad zone")
    if "open" in req:
        ctl(f"setZoneData?zone={z}&zoneSetting={1 if req['open'] else 0}")
    if "percent" in req:
        p = int(req["percent"])
        if not 0 <= p <= 100 or p % 5:
            raise ValueError("percent must be 0-100 in steps of 5")
        ctl(f"setZoneData?zone={z}&userPercentSetting={p}")
    _zone_cache["t"] = 0.0


class Handler(BaseHTTPRequestHandler):
    server_version = "myair3-webcontrol/1.0"

    def _allowed(self):
        ip = ipaddress.ip_address(self.client_address[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in n for n in ALLOW)

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        path = urllib.parse.urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                return self._send(200, INDEX, "text/html; charset=utf-8")
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                if name in STATIC:
                    return self._send(200, *STATIC[name])
                return self._send(404, {"error": "not found"})
            if path == "/api/state":
                return self._send(200, state(max_age=4.0))
            return self._send(404, {"error": "not found"})
        except Exception as e:  # controller unreachable etc.
            return self._send(502, {"error": str(e)})

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        # Block cross-site form posts from other origins in the browser.
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self._send(415, {"error": "json only"})
        path = urllib.parse.urlparse(self.path).path
        try:
            n = min(int(self.headers.get("Content-Length", "0")), 4096)
            req = json.loads(self.rfile.read(n) or b"{}")
            sysinfo = read_system()
            if path == "/api/system":
                apply_system(req, sysinfo)
            elif path == "/api/zone":
                apply_zone(req, sysinfo)
            else:
                return self._send(404, {"error": "not found"})
            return self._send(200, state())
        except (ValueError, KeyError, TypeError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(502, {"error": str(e)})

    def log_message(self, fmt, *args):
        if self.command == "POST":
            super().log_message(fmt, *args)


if __name__ == "__main__":
    print(f"MyAir3 WebControl on http://{BIND}:{PORT} -> controller {HOST}; allow {ALLOW}", flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
