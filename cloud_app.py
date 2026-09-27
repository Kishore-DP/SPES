# cloud_app.py — SPES v2 cloud backend (replaces the laptop; runs on Render).
#
# Laptop-free: the ESP32 brain POSTs mic audio (+ a camera photo) here; we do
# STT + classify + AI + OCR + nav + SOS + memory, then RETURN the reply as audio
# (PCM) for the ESP32 to play on its speaker. Also serves the companion website
# and stores its data.
#
# NO laptop-hardware libs (no cv2 / sounddevice / playsound). Reuses the clean
# helper modules spes_memory / spes_navigation / spes_sos.
#
# Run locally:  python cloud_app.py       (uses key.txt)
# On Render:    gunicorn cloud_app:app    (set GEMINI_KEY env var)

import asyncio
import glob
import json
import os
import time
import urllib.parse
from flask import Flask, request, send_from_directory, jsonify, Response
from google import genai
from google.genai import types
import edge_tts
import miniaudio
import requests

import spes_memory as memory
import spes_navigation as nav
import spes_sos as sos

# --- Gemini key: env var on the cloud, key.txt locally ---
API_KEY = os.environ.get("GEMINI_KEY")
if not API_KEY and os.path.exists("key.txt"):
    API_KEY = open("key.txt").read().strip()
client = genai.Client(api_key=API_KEY)

# Optional shared secret: if SPES_TOKEN env var is set, /brain requires the
# header  X-SPES-Token: <that value>  (so randoms can't burn your Gemini quota).
SPES_TOKEN = os.environ.get("SPES_TOKEN")

MODELS = [
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
    "gemini-2.0-flash",
]
NO_SPEECH_MSG = "Sorry, I could not catch that. Please try again."

app = Flask(__name__)


@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Expose-Headers"] = "X-SPES-Text"
    return resp


def gemini(contents):
    for model in MODELS:
        for _ in range(3):
            try:
                return client.models.generate_content(model=model, contents=contents).text.strip()
            except Exception as e:
                msg = str(e)
                if "503" in msg or "UNAVAILABLE" in msg or "429" in msg:
                    time.sleep(2)
                    continue
                break
    raise RuntimeError("All models busy")


VOICE_MAP = {
    "Aria": "en-US-AriaNeural", "Guy": "en-US-GuyNeural", "Jenny": "en-US-JennyNeural",
    "Sonia": "en-GB-SoniaNeural", "Ryan": "en-GB-RyanNeural",
    "Neerja": "en-IN-NeerjaNeural", "Prabhat": "en-IN-PrabhatNeural",
}
DEFAULT_VOICE = "en-US-AriaNeural"
PCM_RATE = 16000   # what the ESP32 plays (mono); it upsamples to the amp rate


def text_to_pcm(text, voice="Aria"):
    """TTS the text and return raw 16kHz mono 16-bit PCM (for the ESP32 speaker)."""
    tts_voice = VOICE_MAP.get(voice, DEFAULT_VOICE)
    async def _gen():
        await edge_tts.Communicate(text, tts_voice).save("reply.mp3")
    asyncio.run(_gen())
    data = open("reply.mp3", "rb").read()
    dec = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16,
                           nchannels=1, sample_rate=PCM_RATE)
    return bytes(dec.samples)


def ocr_image(image_bytes):
    part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    prompt = ("Read all the text in this image and return it exactly as written. "
              "Only return the text. If there is no readable text, reply: "
              "I could not find any text to read.")
    return gemini([prompt, part])


# ---------------- storage ----------------
APP_DIR = "app_data"
RECENT_DIR = os.path.join("pictures", "recent")
SAVED_DIR = os.path.join("pictures", "saved")
for _d in (APP_DIR, RECENT_DIR, SAVED_DIR):
    os.makedirs(_d, exist_ok=True)
MAX_RECENT = 10


def _load(name, default):
    p = os.path.join(APP_DIR, name)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return default


def _save(name, data):
    json.dump(data, open(os.path.join(APP_DIR, name), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)


def save_picture(img_bytes, folder):
    fname = time.strftime("%Y%m%d-%H%M%S") + ".jpg"
    open(os.path.join(folder, fname), "wb").write(img_bytes)
    if folder == RECENT_DIR:
        for old in sorted(glob.glob(os.path.join(RECENT_DIR, "*.jpg")))[:-MAX_RECENT]:
            try:
                os.remove(old)
            except Exception:
                pass
    return fname


def _list_pics(folder, seg, limit=None):
    pics = sorted(glob.glob(os.path.join(folder, "*.jpg")), reverse=True)
    if limit:
        pics = pics[:limit]
    return [{"name": os.path.basename(p), "time": os.path.getmtime(p),
             "url": f"/pictures/{seg}/{os.path.basename(p)}"} for p in pics]


def get_settings():
    return _load("settings.json", {"voice": "Aria", "wakeWord": "Max"})


# ---------------- brain state ----------------
_mem = memory.load_mem()
_last_answer = ""
# The phone app pushes the user's real GPS here (POST /api/location). Nav/SOS
# use it, because ip-api geolocation from Render would return the SERVER's
# location, not the user's. None until the phone reports.
_device_location = None   # (lat, lon, city)


def device_location():
    """User's location: the phone's reported GPS if available, else ip-api
    (which on the cloud is only a rough fallback = server location)."""
    return _device_location or nav.current_location()
VOLUME_STEPS = ["-50%", "-25%", "+0%", "+25%", "+50%", "+100%"]
_vol = 2

CONTROL_MAP = [
    ("STOP", ["stop", "be quiet", "never mind", "cancel that"]),
    ("REPEAT", ["say that again", "repeat that", "repeat", "what did you say"]),
    ("FORGET", ["forget everything", "clear your memory", "forget me"]),
    ("LOUDER", ["louder", "volume up", "turn it up"]),
    ("SOFTER", ["quieter", "softer", "volume down", "turn it down"]),
]


def classify(audio_bytes, context=""):
    part = types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav")
    memblock = (context + "\n\n") if context else ""
    prompt = (memblock +
        "The audio is a spoken command to SPES, an assistant for a visually "
        "impaired user. Reply EXACTLY:\n"
        "MODE: READ or CAPTURE or ASK or CONTROL or NAVIGATE or SOS\n"
        "COMMAND: <if CONTROL: STOP/REPEAT/FORGET/LOUDER/SOFTER else blank>\n"
        "DESTINATION: <if NAVIGATE: the place else blank>\n"
        "HEARD: <what the user said>\n"
        "ANSWER: <if ASK: a brief 1-3 sentence spoken answer using memory above else blank>\n\n"
        "READ = read text in front of them. CAPTURE = just take/save a photo, no "
        "reading. NAVIGATE = directions to a place. SOS = emergency/help. "
        "CONTROL = command the device (stop/repeat/forget/louder/softer). "
        "ASK = a general question.")
    reply = gemini([prompt, part])
    mode, command, dest, heard, answer = "ASK", "", "", "", ""
    for line in reply.splitlines():
        u = line.upper()
        if u.startswith("MODE:"):
            for m in ("CAPTURE", "READ", "NAVIGATE", "SOS", "CONTROL"):
                if m in u:
                    mode = m
                    break
            else:
                mode = "ASK"
        elif u.startswith("COMMAND:"):
            command = line.split(":", 1)[1].strip().upper()
        elif u.startswith("DESTINATION:"):
            dest = line.split(":", 1)[1].strip()
        elif u.startswith("HEARD:"):
            heard = line.split(":", 1)[1].strip()
        elif u.startswith("ANSWER:"):
            answer = line.split(":", 1)[1].strip()
    low = heard.lower()
    for cmd, phrases in CONTROL_MAP:
        if any(p in low for p in phrases):
            mode, command = "CONTROL", cmd
            break
    if any(p in low for p in ["capture this", "take a photo", "take a picture", "save this photo"]):
        mode = "CAPTURE"
    if any(p in low for p in ["sos", "emergency", "help me", "in danger", "call for help"]):
        mode = "SOS"
    return mode, command, dest, heard, answer


def handle_control(command):
    global _vol
    if command == "REPEAT":
        return _last_answer or "I have nothing to repeat yet."
    if command == "FORGET":
        _mem["summary"] = ""; _mem["history"] = []; memory.save_mem(_mem)
        return "I have cleared my memory."
    if command == "LOUDER":
        _vol = min(_vol + 1, len(VOLUME_STEPS) - 1); return "Okay, louder."
    if command == "SOFTER":
        _vol = max(_vol - 1, 0); return "Okay, softer."
    if command == "STOP":
        return "Okay, stopping."
    return "Sorry, I did not understand that command."


@app.route("/brain", methods=["POST"])
def brain():
    """The ESP32 brain POSTs multipart: 'audio' (WAV) + optional 'image' (JPEG,
    grabbed from the CAM). Returns the spoken reply as 16kHz mono PCM, with the
    text in the X-SPES-Text header."""
    global _last_answer
    if SPES_TOKEN and request.headers.get("X-SPES-Token") != SPES_TOKEN:
        return Response("unauthorized", status=401)
    audio_bytes = request.files["audio"].read() if "audio" in request.files else request.data
    image_bytes = request.files["image"].read() if "image" in request.files else None
    if not audio_bytes:
        return Response("No audio", status=400)

    context = memory.build_context(_mem)
    try:
        mode, command, dest, heard, answer = classify(audio_bytes, context)
    except Exception as e:
        print("classify error:", e)
        return Response("error", status=500)
    print("[/brain]", mode, command, dest, "|", heard)

    if mode == "CONTROL":
        spoken = handle_control(command)
    elif mode == "NAVIGATE":
        try:
            spoken = nav.navigate(dest, origin=device_location())
        except Exception:
            spoken = "Sorry, navigation is not available right now."
        memory.remember_turn(_mem, heard or ("navigate to " + dest), "Gave directions to " + dest, client, MODELS)
    elif mode == "SOS":
        if image_bytes:
            save_picture(image_bytes, SAVED_DIR)
        spoken, _ = sos.send_sos(image_bytes, location=device_location())
    elif mode == "CAPTURE":
        if image_bytes:
            save_picture(image_bytes, SAVED_DIR)
            spoken = "Photo captured and saved."
        else:
            spoken = "I did not get a photo from the camera."
    elif mode == "READ":
        if image_bytes:
            text = ocr_image(image_bytes)
            save_picture(image_bytes, RECENT_DIR)
            spoken = "Here is what I can read. " + text
        else:
            spoken = "I did not get a photo from the camera to read."
        memory.remember_turn(_mem, heard or "read this", spoken, client, MODELS)
    else:  # ASK
        spoken = answer or NO_SPEECH_MSG
        memory.remember_turn(_mem, heard, spoken, client, MODELS)

    _last_answer = spoken
    print("[/brain] reply:", spoken[:80])
    pcm = text_to_pcm(spoken, get_settings().get("voice", "Aria"))
    resp = Response(pcm, mimetype="application/octet-stream")
    resp.headers["X-SPES-Text"] = urllib.parse.quote(spoken)
    return resp


# ---------------- companion app API ----------------
@app.route("/api/todos", methods=["GET"])
def todos_get():
    return jsonify(_load("todos.json", []))


@app.route("/api/todos", methods=["POST"])
def todos_add():
    text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    if not text:
        return Response("empty", status=400)
    todos = _load("todos.json", [])
    todos.append({"id": int(time.time() * 1000), "text": text, "done": False})
    _save("todos.json", todos)
    return jsonify(todos)


@app.route("/api/todos/<int:tid>", methods=["PATCH"])
def todos_toggle(tid):
    todos = _load("todos.json", [])
    for t in todos:
        if t["id"] == tid:
            t["done"] = not t["done"]
    _save("todos.json", todos)
    return jsonify(todos)


@app.route("/api/todos/<int:tid>", methods=["DELETE"])
def todos_del(tid):
    _save("todos.json", [t for t in _load("todos.json", []) if t["id"] != tid])
    return jsonify(_load("todos.json", []))


@app.route("/api/contacts", methods=["GET"])
def contacts_get():
    return jsonify(_load("contacts.json", []))


@app.route("/api/contacts", methods=["POST"])
def contacts_add():
    b = request.get_json(silent=True) or {}
    name, phone = (b.get("name") or "").strip(), (b.get("phone") or "").strip()
    if not name or not phone:
        return Response("name and phone required", status=400)
    c = _load("contacts.json", [])
    c.append({"id": int(time.time() * 1000), "name": name, "phone": phone})
    _save("contacts.json", c)
    return jsonify(c)


@app.route("/api/contacts/<int:cid>", methods=["DELETE"])
def contacts_del(cid):
    _save("contacts.json", [c for c in _load("contacts.json", []) if c["id"] != cid])
    return jsonify(_load("contacts.json", []))


@app.route("/api/settings", methods=["GET"])
def settings_get():
    return jsonify(get_settings())


@app.route("/api/settings", methods=["POST"])
def settings_set():
    b = request.get_json(silent=True) or {}
    s = get_settings()
    if "voice" in b:
        s["voice"] = b["voice"]
    if "wakeWord" in b:
        s["wakeWord"] = b["wakeWord"]
    _save("settings.json", s)
    return jsonify(s)


@app.route("/api/voices", methods=["GET"])
def voices_get():
    return jsonify(list(VOICE_MAP.keys()))


@app.route("/api/pictures", methods=["GET"])
def pictures_recent():
    return jsonify(_list_pics(RECENT_DIR, "recent", MAX_RECENT))


@app.route("/api/pictures/saved", methods=["GET"])
def pictures_saved():
    return jsonify(_list_pics(SAVED_DIR, "saved"))


@app.route("/api/pictures/saved/<name>", methods=["DELETE"])
def pictures_saved_del(name):
    try:
        os.remove(os.path.join(SAVED_DIR, os.path.basename(name)))
    except Exception:
        pass
    return jsonify(_list_pics(SAVED_DIR, "saved"))


@app.route("/pictures/<folder>/<name>")
def pictures_serve(folder, name):
    if folder not in ("recent", "saved"):
        return Response("not found", status=404)
    return send_from_directory(os.path.join("pictures", folder), name)


@app.route("/api/location", methods=["GET"])
def location_get():
    loc = device_location()
    if not loc:
        return jsonify({"ok": False})
    lat, lon, city = loc
    return jsonify({"ok": True, "lat": lat, "lon": lon, "city": city,
                    "maps": f"https://maps.google.com/?q={lat},{lon}",
                    "source": "phone" if _device_location else "ip"})


@app.route("/api/location", methods=["POST"])
def location_set():
    """The phone app posts the browser's real GPS here so NAVIGATE/SOS use the
    user's location, not the cloud server's."""
    global _device_location
    b = request.get_json(silent=True) or {}
    try:
        _device_location = (float(b["lat"]), float(b["lon"]), b.get("city", "your location"))
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "error": "need lat and lon"}), 400


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "service": "SPES cloud"})


@app.route("/app")
def companion_app():
    return send_from_directory("app", "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"SPES cloud backend on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
