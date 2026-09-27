# MAX SPES — Full Project Memory / Handoff

> **Paste this whole file into a Claude chat to give it the complete context of my
> "MAX SPES" project.** It replaces the memory the Claude Code assistant had.
> I'm Kishore (GitHub `Kishore-DP`), a beginner/intermediate embedded + web
> learner. Teach one stage at a time, give COMPLETE code, wire things pin-by-pin,
> and test before advancing. When I give code to run I usually want it pasted in
> chat. My Windows Desktop is OneDrive-redirected: real path is
> `C:\Users\kisho\OneDrive\Desktop`.

---

## 1. What SPES is
**SPES = See, Process, Enhance, Speak** — an AI + IoT hands-free wearable assistant
(originally an aid for the visually impaired, pitched for the VIT Vellore hackathon).
You speak a command; it decides what to do (read text via camera, answer a question,
navigate, SOS, save a photo, control the device), and replies with speech.

Wake/assistant name = **"Max"**.

## 2. The GOAL (v2 — "MAX SPES", current direction)
A **fully independent, laptop-free wearable**: spectacles with battery + buck +
ESP32-CAM + ESP32-WROOM-32D + a speaker (amp) hot-glued on. Turn on the battery,
**tap a touch pad and speak** ("Max, ..."), the AI processes in the cloud and
**replies through the speaker**, and saves things to a hosted website when asked.
Uses the **user's mobile phone hotspot** (2.4GHz) for internet.

**My 6 requirements + decisions:**
1. Whole project works WITHOUT a laptop (no `python server.py` on a laptop). ✅ done via cloud.
2. Only respond to what I say after the wake word. → **DECISION: touch/button to talk** (not always-listening ML; classic ESP32-WROOM can't do good on-device wake word — that needs an ESP32-S3).
3. Host the companion website. ✅ done (Render).
4. AI actions persist to the website (e.g. "save this picture" → shows in the site's Saved gallery). Backend supports it.
5. Fast, smart AI reachable from the ESP32 directly (no laptop/cmd). ✅ cloud, ~4.4s.
6. WiFi = my mobile hotspot (2.4GHz).
Target reply time ~3s (realistic 4–8s with free cloud + cold starts).

## 3. CURRENT STATE (as of 2026-09-27) — what's DONE and LIVE
- **Cloud backend LIVE on Render:** **https://spes-kbho.onrender.com**
  - `cloud_app.py` (Flask, gunicorn). Free tier. Env var `GEMINI_KEY` set (my Gemini key).
  - The ESP32 brain will POST audio (+ a CAM photo) to `/brain`; the cloud does
    STT + classify + AI + OCR + nav/SOS + memory, and RETURNS the reply as 16kHz
    mono PCM audio (for the ESP32 speaker) with the text in header `X-SPES-Text`.
  - Verified working over the internet: `/api/health`, `/api/settings`,
    `/api/voices`, `/app` (website), and `/brain` ASK "capital of France"→"Paris"
    + audio in **4.4s** round-trip (warm).
  - Auto-redeploys from GitHub `master` pushes.
- **Companion website LIVE:** **https://spes-kbho.onrender.com/app**
  - Single-file PWA (`app/index.html`, vanilla HTML/CSS/JS). Dark violet/cyan UI,
    bottom nav, 6 sections: To-do · SOS (guardians + SOS button) · AI assistant
    (voice + activation word) · Bluetooth (placeholder) · Pictures (Recent auto /
    Saved by "capture this") · Location (+ find-my-SPES beep).
- **GitHub repo:** `Kishore-DP/SPES` (PRIVATE). Local: `C:\Users\kisho\OneDrive\Desktop\SPES`.
  Commit trailer used: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **All AI features work** (in the cloud backend): READ (OCR a photo), ASK
  (question→answer), CONTROL (stop/repeat/forget/louder/softer), NAVIGATE
  (turn-by-turn), SOS (guardian alert), CAPTURE (save photo, no reading),
  plus conversation MEMORY across turns.
- **Hardware all confirmed working** (in the earlier laptop-based version):
  ESP32-CAM streams + `/capture`, ESP32-WROOM brain, INMP441 mic (soldered).

## 4. Repo file structure (`Kishore-DP/SPES`)
- `cloud_app.py` — **the cloud backend (v2, current).** No laptop-hardware libs.
- `server.py` — the OLDER laptop backend (plays audio locally via playsound; used
  in the laptop-based demo). Kept for reference.
- `combined_spes.py`, `spes_jarvis.py` — laptop voice flow + wake-word loop (legacy).
- `spes_livekit.py` + `run_ted.bat` — a separate LiveKit real-time console agent
  (L2–L5 done: wake/sleep "Max", memory, control, camera read tool). Laptop-only,
  needs LiveKit Cloud; NOT used by the hardware.
- `spes_memory.py` (+ `spes_memory.json`, gitignored) — conversation memory.
- `spes_navigation.py` — free turn-by-turn nav (OSM Nominatim + OSRM + ip-api geoloc).
- `spes_sos.py` — Guardian SOS via ntfy.sh (free push; set `GUARDIAN_TOPIC`).
- `app/index.html` — the companion PWA.
- `firmware/esp32cam_CameraWebServer.ino` — working CAM sketch (paste into the
  Arduino CameraWebServer EXAMPLE, not a blank sketch).
- `firmware/esp32wroom_brain.ino` — INMP441 mic → backend brain sketch.
- `requirements.txt`, `Procfile` — Render deploy config.
- `key.txt` (gitignored) — the Gemini API key (also set as Render env `GEMINI_KEY`).
- Gitignored: `key.txt`, `.env`, `spes_memory.json`, `app_data/`, `pictures/`.

## 5. Hardware & wiring (the physical device)
- **ESP32-CAM (AI-Thinker) = "eye"** — camera + WiFi only. OV3660 sensor.
  Flash via the ESP32-CAM-MB base board (own USB) or FTDI (TXD→U0R, RXD→U0T, GND;
  IO0→GND during upload; hold RST, click Upload, release on "Connecting…").
- **ESP32-WROOM-32D = "brain"** — flashes via own USB (board "ESP32 Dev Module",
  hold BOOT if stuck at Connecting). Runs the mic + talks to the cloud.
- **INMP441 I2S mic → brain:** VDD→3V3 (NOT 5V), GND→GND, L/R→GND (LEFT slot),
  WS→GPIO25, SCK→GPIO33, SD→GPIO32. Read via ESP_I2S.h (I2SClass, 32-bit MONO,
  I2S_STD_SLOT_LEFT; sample>>14). Mic is soldered + working.
- **Power:** 2× Li-ion in SERIES = 2S ~8V pack → **LM2596 buck** → ~5.0V → 5V/VIN
  of both boards, common ground. NO boost needed (pack is already 8V). Buck must
  source ≥1A. ⚠️ TP4056 is a 1-cell charger and CANNOT charge a 2S pack — charge
  cells INDIVIDUALLY on a standalone 18650 slot charger (each to ~4.2V).
- **Speaker (pending amp):** I have an MX36-07 4Ω 3W passive speaker. The ESP32
  CANNOT drive it directly — needs a small amp. Plan = **MAX98357A I2S amp**
  (~₹100) → speaker. WiFi + I2S coexist perfectly (no radio conflict). The cloud
  already returns PCM ready to stream to the amp.

## 6. HARD-WON LESSONS / GOTCHAS (avoid repeating these)
- **Camera i2c/SCCB errors** (`I2C bus busy`, `Setting framesize 1600x1200 failed`,
  `Camera init 0x20002/0x106`, reboot loop) = marginal POWER + flaky ribbon. FIX:
  set `config.xclk_freq_hz = 10000000` (10MHz) + `config.frame_size = FRAMESIZE_SVGA`
  (not UXGA) + **reseat the camera ribbon firmly** + power via solid USB. Do NOT
  add `WiFi.config()` static IP (it caused malloc-fail crashes). Paste the CAM code
  into the CameraWebServer EXAMPLE (needs board_config.h etc.), never a blank sketch.
- **Bluetooth audio on the ESP32 = ABANDONED.** One ESP32 can't do WiFi + Classic
  BT (A2DP) at once (shared radio — WiFi HTTP returns -1/-2 while BT streams). And
  the WROOM has only ~129KB free heap with BT → can't buffer answers (OOM crash).
  A two-chip split (separate WiFi chip + WROOM for BT) avoids the radio conflict but
  keeps the BT RAM limit + adds hard inter-chip audio transfer — not worth it.
  **Use a wired MAX98357A I2S speaker instead.**
- **ESP32 is 2.4GHz WiFi only** (use the phone hotspot on 2.4GHz, not 5GHz).
- **Gemini:** use model `gemini-flash-lite-latest` first (fastest, least 503s);
  `gemini-2.5-flash` 404s. SDK `google-genai` (`from google import genai`). Gemini
  accepts audio directly (transcribes + answers in one call). Retry on 503/429.
- **TTS:** `edge-tts` (free, natural). Decode mp3→PCM with `miniaudio` (no ffmpeg).
- **Render free tier:** cold-starts after ~15 min idle (first request ~30–60s).
  Add a keep-alive ping later. Its disk is EPHEMERAL — todos/pictures reset on
  restart; for permanent storage add a DB (Supabase/Firebase) later.
- **The camera is on the hotspot LAN; the cloud can't reach it.** So the ESP32
  brain must BRIDGE: grab a photo from the CAM (both on the hotspot) and send
  audio + photo TOGETHER to the cloud each request.
- Buttons on ESP32: **EN = reset** (not a GPIO), **BOOT = GPIO0** (used for
  push-to-talk currently; will become a touch pad).

## 7. WHAT'S PENDING (next steps for MAX SPES)
1. **Wire the ESP32 brain → cloud** (the core "independent" step). Modify the brain
   sketch to: grab a CAM photo + record mic → POST both (multipart) to
   `https://spes-kbho.onrender.com/brain` over HTTPS (needs `WiFiClientSecure` +
   `client.setInsecure()`), read the returned PCM audio + `X-SPES-Text`. Test the
   text reply now; audio playback waits for the amp.
2. **Touch-to-talk** — replace the BOOT button with a capacitive touch pad on the
   glasses (WROOM touch pins T0–T9).
3. **MAX98357A speaker** — I2S out so the glasses play the cloud's audio reply.
4. **Cold-start keep-alive** — periodic ping so Render stays warm.
5. **Set the ESP32s to the mobile hotspot** (2.4GHz).
6. **Permanent storage** — a DB so todos/pictures survive Render restarts.
7. **Final assembly** — battery + buck + boards + speaker glued onto the glasses.

## 8. Working URLs / refs
- Cloud backend + website: **https://spes-kbho.onrender.com** (`/app`, `/brain`, `/api/*`).
- Repo: `github.com/Kishore-DP/SPES` (private).
- Gemini key: in `key.txt` locally and Render env var `GEMINI_KEY` (don't commit it).
- Nav APIs (free, no key): Nominatim geocode, OSRM routing, ip-api.com geoloc.
- SOS: ntfy.sh push (guardian subscribes to the topic in the ntfy app).
