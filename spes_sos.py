# spes_sos.py — Emergency SOS / Guardian mode for SPES
#
# When the user triggers SOS, we alert a trusted GUARDIAN (not the police
# directly) by pushing a notification with the user's location and a photo
# from the camera. The guardian gets it on their phone via the free ntfy app.
#
# SETUP (guardian side, one time):
#   1. Install the "ntfy" app (Android/iOS) or open https://ntfy.sh
#   2. Subscribe to the topic in GUARDIAN_TOPIC below.
# That's it -- no account, no key. Anyone subscribed to the topic gets the SOS.
#
# NOTE: this notifies a trusted contact who can then involve emergency services.
# Real deployment should route through a verified emergency service or a
# registered guardian, never straight into a police system.

import requests
import spes_navigation as nav

NTFY = "https://ntfy.sh"
# CHANGE THIS to your own private topic and have your guardian subscribe to it.
GUARDIAN_TOPIC = "spes-guardian-kishore-CHANGE-ME"


def send_sos(photo_bytes=None):
    """Push an SOS alert (location + optional photo) to the guardian's ntfy
    topic. Returns (spoken_confirmation, maps_link)."""
    loc = nav.current_location()
    if loc:
        lat, lon, city = loc
        maps = f"https://maps.google.com/?q={lat},{lon}"
        summary = f"Location: {city} ({lat:.5f}, {lon:.5f})  {maps}"
    else:
        maps = None
        summary = "Location: unknown"

    message = "EMERGENCY! The SPES user has triggered an SOS. " + summary
    headers = {
        "Title": "SPES EMERGENCY SOS",
        "Priority": "urgent",
        "Tags": "rotating_light,sos",
    }
    if maps:
        headers["Click"] = maps          # tapping the notification opens the map

    ok = False
    try:
        if photo_bytes:
            # Attach the camera photo; the text goes in the Message header.
            headers["Filename"] = "sos.jpg"
            headers["Message"] = message
            r = requests.put(f"{NTFY}/{GUARDIAN_TOPIC}", data=photo_bytes,
                             headers=headers, timeout=15)
        else:
            r = requests.post(f"{NTFY}/{GUARDIAN_TOPIC}",
                              data=message.encode("utf-8"), headers=headers, timeout=15)
        ok = (r.status_code == 200)
    except Exception as e:
        print("  SOS send error:", e)

    if ok:
        spoken = ("Emergency alert sent to your guardian with your location"
                  + (" and a photo." if photo_bytes else "."))
    else:
        spoken = ("I could not send the emergency alert. "
                  "Please try to call for help directly.")
    return spoken, maps


if __name__ == "__main__":
    print(send_sos()[0])
