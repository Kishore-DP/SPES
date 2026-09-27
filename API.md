# SPES Cloud API & Config Reference

Backend = `cloud_app.py` (Flask/gunicorn) live at **https://spes-kbho.onrender.com**.
This documents the exact contracts so the firmware and website can be rebuilt
without reading the Python.

## `/brain` — the main endpoint (POST)
The ESP32 brain sends the mic recording (+ a camera photo) here; gets back the
spoken reply as audio.

**Request:** `multipart/form-data` with:
| field | type | format |
|-------|------|--------|
| `audio` | file (required) | **WAV**, 16000 Hz, 16-bit, **mono**, PCM. (A raw request body with no multipart is also accepted as the audio.) |
| `image` | file (optional) | **JPEG** from the ESP32-CAM (SVGA ~10–15KB). Only used if the command is READ / CAPTURE / SOS. |

**Auth:** none by default. If the Render env var `SPES_TOKEN` is set, the request
must include header `X-SPES-Token: <that value>` or it returns `401`. **Set this**
so randoms can't burn your Gemini quota.

**Response:** `200`, `Content-Type: application/octet-stream` = **raw PCM audio**,
**16000 Hz, 16-bit, mono** (the reply speech). Header `X-SPES-Text` = the reply
text, URL-encoded. Typical size ~30–60 KB per second of speech (e.g. "The capital
of France is Paris." ≈ 91 KB). **Stream it to the amp** — don't buffer the whole
thing (WROOM RAM is limited; WiFi + I2S coexist so you can read+play in chunks).

**Example:**
```bash
curl -s -X POST https://spes-kbho.onrender.com/brain \
  -H "X-SPES-Token: YOURTOKEN" \
  -F "audio=@question.wav;type=audio/wav" \
  -F "image=@photo.jpg;type=image/jpeg" \
  -D headers.txt -o reply.pcm
# reply text is in headers.txt as X-SPES-Text (url-encoded); reply.pcm is 16k mono PCM
```

The cloud classifies the audio into one MODE and acts:
- **ASK** → Gemini answer. **READ** → OCR the `image`. **CAPTURE** → save `image` to
  Saved gallery, no reading. **NAVIGATE** → spoken turn-by-turn to the spoken place.
  **SOS** → push guardian alert (location + photo) via ntfy. **CONTROL** →
  stop/repeat/forget/louder/softer. Conversation MEMORY is applied to ASK.

## Companion app API (used by the website)
- `GET/POST /api/todos`, `PATCH/DELETE /api/todos/<id>`
- `GET/POST /api/contacts`, `DELETE /api/contacts/<id>` (SOS guardians)
- `GET/POST /api/settings` (`{voice, wakeWord}`), `GET /api/voices`
- `GET /api/pictures` (Recent, last 10), `GET /api/pictures/saved`,
  `POST /api/pictures/capture` (→ Saved), `DELETE /api/pictures/saved/<name>`,
  `GET /pictures/<recent|saved>/<name>`
- `GET /api/location` (returns phone GPS if reported, else IP), **`POST /api/location`**
  (`{lat,lon,city?}` — the phone app reports its real GPS here)
- `GET /api/health`, `GET /app` (the website)

## Location sourcing (was a cloud bug — now fixed)
`ip-api` geolocation from Render returns the **server's** location, not yours. So
the **phone app POSTs the browser's GPS to `/api/location`** on load and when the
Location tab opens; `device_location()` uses that for NAVIGATE and SOS. If the
phone never reports, it falls back to IP (rough, server-side). For a GPS-accurate
device, add a GPS module to the ESP32 later and POST its fix instead.

## Render environment variables
| var | required | purpose |
|-----|----------|---------|
| `GEMINI_KEY` | **yes** | Gemini API key (also in local `key.txt`) |
| `GUARDIAN_TOPIC` | recommended | your private ntfy topic for SOS (guardian subscribes in the ntfy app). Falls back to a placeholder. |
| `SPES_TOKEN` | recommended | shared secret; if set, `/brain` requires header `X-SPES-Token` |
| `PORT` | auto | Render sets it; `gunicorn ... --bind 0.0.0.0:$PORT` |

## Hardcoded IPs / SSIDs (change per network)
- `firmware/esp32cam_CameraWebServer.ino`: `ssid`/`password` (WiFi).
- `firmware/esp32wroom_brain.ino`: `ssid`/`password`, and the target URL/host.
- `combined_spes.py` (legacy laptop version): `ESP32_CAM_URL = http://192.168.29.138/capture`.
- On the phone hotspot the CAM's IP changes → the CAM now advertises **mDNS
  `spescam.local`** (`MDNS.begin("spescam")`); the brain resolves that instead of
  a hardcoded IP.

## Pin map (ESP32-WROOM-32D brain)
| use | pins | I2S |
|-----|------|-----|
| INMP441 mic (input) | WS=25, SCK=33, SD=32 (VDD→3V3) | I2S port 0 |
| **MAX98357A amp (output) — RESERVED** | BCLK=26, LRC=27, DIN=22 | I2S port 1 |
| **Touch-to-talk — RESERVED** | GPIO4 (touch T0) | — |
| Push-to-talk (current) | BOOT = GPIO0 | — |
No conflicts: mic (25/33/32), amp (26/27/22), touch (4) are all separate.
(ESP32-CAM is a *separate board* — its pins don't affect the brain.)

## Current firmware behaviour
- `esp32wroom_brain.ino` (updated to cloud version): on tap (GPIO4 touch, or BOOT),
  fetch a JPEG from the CAM (`spescam.local/capture`), record 4 s of mic (16 kHz
  mono WAV), POST both as multipart to `https://spes-kbho.onrender.com/brain` over
  HTTPS (`WiFiClientSecure` + `setInsecure()`), read `X-SPES-Text` and print it.
  Audio playback to the amp is the next step (pins reserved above).
- `esp32cam_CameraWebServer.ino`: STA mode, serves `/capture` + `/stream`, advertises
  mDNS `spescam.local`. Camera stability: `xclk 10 MHz` + `FRAMESIZE_SVGA`.
