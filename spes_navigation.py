# spes_navigation.py — voice turn-by-turn navigation for SPES (free, no API key)
#
# Uses OpenStreetMap Nominatim (geocoding) + OSRM (routing) — both free, no key.
# geocode(place)         -> (lat, lon, name)
# current_location()     -> (lat, lon, city)  [IP-based; a GPS stand-in for now]
# navigate(dest, origin) -> a spoken turn-by-turn string
#
# LATER: replace current_location() with a real GPS reading from the device
# (e.g. a GPS module on the ESP32, or the phone's location via the web app).

import requests

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSRM = "https://router.project-osrm.org/route/v1/driving"
HEADERS = {"User-Agent": "SPES-assistive-device/1.0"}


def geocode(place):
    """Turn a place name into coordinates. Returns (lat, lon, display_name)."""
    r = requests.get(NOMINATIM,
                     params={"q": place, "format": "json", "limit": 1},
                     headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    d = data[0]
    return float(d["lat"]), float(d["lon"]), d["display_name"]


def current_location():
    """Best-effort current location via IP geolocation (GPS stand-in).
    Returns (lat, lon, city) or None."""
    try:
        g = requests.get("http://ip-api.com/json/", timeout=10).json()
        if g.get("status") == "success":
            return g["lat"], g["lon"], g.get("city", "your location")
    except Exception:
        pass
    return None


def get_route(o_lat, o_lon, d_lat, d_lon):
    """Get a driving route with steps. Returns the OSRM route dict or None."""
    url = f"{OSRM}/{o_lon},{o_lat};{d_lon},{d_lat}"
    r = requests.get(url, params={"overview": "false", "steps": "true"}, timeout=20)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != "Ok" or not j.get("routes"):
        return None
    return j["routes"][0]


def _dir(mod):
    return {
        "left": "left", "right": "right",
        "slight left": "slightly left", "slight right": "slightly right",
        "sharp left": "sharply left", "sharp right": "sharply right",
        "straight": "straight ahead", "uturn": "around",
    }.get(mod, mod)


def step_instruction(step):
    """Turn one OSRM step into a spoken instruction."""
    m = step["maneuver"]
    typ = m["type"]
    mod = m.get("modifier", "")
    road = step.get("name", "")
    dist = int(step["distance"])
    onto = f" onto {road}" if road else ""

    if typ == "arrive":
        return "You have arrived at your destination."
    if mod == "uturn":
        base = f"Make a U-turn{onto}"
    elif typ == "depart":
        base = f"Start driving{onto}"
    elif typ == "turn":
        base = f"Turn {_dir(mod)}{onto}"
    elif typ == "new name":
        base = f"Continue{onto}"
    elif typ == "merge":
        base = f"Merge {_dir(mod)}{onto}"
    elif typ in ("roundabout", "rotary"):
        base = f"Take the roundabout{onto}"
    elif typ == "fork":
        base = f"Keep {_dir(mod)}{onto}"
    elif typ == "end of road":
        base = f"At the end of the road, turn {_dir(mod)}{onto}"
    elif typ in ("continue", "on ramp", "off ramp"):
        base = f"Continue {_dir(mod)}{onto}".strip()
    else:
        base = f"Continue{onto}".strip()

    if dist >= 1000:
        return f"In {dist / 1000:.1f} kilometers, {base}."
    if dist > 0:
        return f"In {dist} meters, {base}."
    return base + "."


def navigate(destination, origin=None):
    """Return a full spoken turn-by-turn route to `destination`.
    `origin` is (lat, lon, name); if None we use the current (IP) location."""
    dest = geocode(destination)
    if not dest:
        return f"Sorry, I could not find {destination}."
    if origin is None:
        origin = current_location()
        if origin is None:
            return "Sorry, I could not determine your current location."

    o_lat, o_lon, o_name = origin
    d_lat, d_lon, d_name = dest
    route = get_route(o_lat, o_lon, d_lat, d_lon)
    if not route:
        return f"Sorry, I could not find a route to {destination}."

    dist_km = route["distance"] / 1000
    dur_min = int(route["duration"] / 60)
    steps = route["legs"][0]["steps"]

    when = "less than a minute" if dur_min < 1 else f"about {dur_min} minutes"
    lines = [f"Starting navigation to {destination}. "
             f"The trip is {dist_km:.1f} kilometers and {when}."]
    for s in steps:
        instr = step_instruction(s)
        if instr:
            lines.append(instr)
    return " ".join(lines)


if __name__ == "__main__":
    import sys
    dest = " ".join(sys.argv[1:]) or "Vellore Institute of Technology"
    print(navigate(dest))
