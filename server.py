# server.py — SPES Step B6: the backend server the ESP32s talk to
#
# Endpoints:
#   GET  /       -> health check ("SPES backend is alive")
#   POST /read   -> send an image, get back spoken audio of the text (OCR)
#   POST /ask    -> send a voice recording, get back the spoken AI answer
#
# Run it with:  python server.py
# It listens on all network interfaces at port 5000.

import asyncio
import time
import urllib.parse
from flask import Flask, request, send_file, Response
from google import genai
from google.genai import types
import edge_tts
import miniaudio

# --- Load API key + connect to Gemini ---
with open("key.txt") as f:
    API_KEY = f.read().strip()
client = genai.Client(api_key=API_KEY)

MODELS = [
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
]

NO_SPEECH_MSG = "Sorry, I could not catch that. Please try again."

app = Flask(__name__)


# Allow the web app (running on a different origin) to call this backend,
# and let it read the recognized text from the X-SPES-Text response header.
@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Expose-Headers"] = "X-SPES-Text"
    return resp


# ---------- shared helpers ----------
def gemini(contents):
    """Call Gemini, trying several models with retries on 'busy' errors."""
    for model in MODELS:
        for attempt in range(3):
            try:
                resp = client.models.generate_content(model=model, contents=contents)
                return resp.text.strip()
            except Exception as e:
                msg = str(e)
                if "503" in msg or "UNAVAILABLE" in msg or "429" in msg:
                    time.sleep(3)
                    continue
                else:
                    break
    raise RuntimeError("All models busy")


# Friendly voice names -> edge-tts voices (the app's voice picker uses these).
VOICE_MAP = {
    "Aria": "en-US-AriaNeural",
    "Guy": "en-US-GuyNeural",
    "Jenny": "en-US-JennyNeural",
    "Sonia": "en-GB-SoniaNeural",
    "Ryan": "en-GB-RyanNeural",
    "Neerja": "en-IN-NeerjaNeural",
    "Prabhat": "en-IN-PrabhatNeural",
}
DEFAULT_VOICE = "en-US-AriaNeural"


def text_to_mp3(text, voice="Aria", filename="reply.mp3"):
    """Turn text into an mp3 file using edge-tts with the chosen voice."""
    tts_voice = VOICE_MAP.get(voice, DEFAULT_VOICE)
    async def _gen():
        communicate = edge_tts.Communicate(text, tts_voice)
        await communicate.save(filename)
    asyncio.run(_gen())
    return filename


def audio_reply(text, voice="Aria", filename="reply.mp3"):
    """Make an mp3 of `text` and return it as the HTTP response.
    The recognized/answer text is also sent in the 'X-SPES-Text' header."""
    text_to_mp3(text, voice, filename)
    resp = send_file(filename, mimetype="audio/mpeg")
    resp.headers["X-SPES-Text"] = urllib.parse.quote(text)  # safe for headers
    return resp


@app.route("/answer", methods=["POST"])
def answer_text():
    """Text question in -> spoken answer out. Used by the app's live voice loop
    (browser does speech-to-text, we do the Gemini answer + voice)."""
    question = (request.form.get("question") or "").strip()
    if not question and request.is_json:
        question = (request.get_json(silent=True) or {}).get("question", "").strip()
    voice = request.form.get("voice", "Aria")
    if not question:
        return Response("No question", status=400)
    prompt = (
        "You are SPES, a friendly voice assistant. Answer clearly and briefly in "
        "1-3 spoken sentences, plain language. Question: " + question
    )
    text = gemini(prompt)
    print("[/answer]", question[:50], "->", text[:60].replace("\n", " "))
    return audio_reply(text, voice)


# ---------- endpoints ----------
@app.route("/")
def home():
    return "SPES backend is alive! Endpoints: POST /read (image), POST /ask (voice)."


@app.route("/read", methods=["POST"])
def read_image():
    # The image can come as a file field 'image' or as the raw request body.
    if "image" in request.files:
        image_bytes = request.files["image"].read()
    else:
        image_bytes = request.data
    if not image_bytes:
        return Response("No image received", status=400)

    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    prompt = (
        "Read all the text in this image and return it exactly as written. "
        "Only return the text, nothing else. If there is no readable text, "
        "reply: I could not find any text to read."
    )
    text = gemini([prompt, image_part])
    print("[/read] ->", text[:80].replace("\n", " "), "...")
    return audio_reply(text, request.form.get("voice", "Aria"))


@app.route("/ask", methods=["POST"])
def ask_voice():
    # The audio can come as a file field 'audio' or as the raw request body.
    if "audio" in request.files:
        audio_bytes = request.files["audio"].read()
        fname = request.files["audio"].filename or "audio.wav"
    else:
        audio_bytes = request.data
        fname = "audio.wav"
    if not audio_bytes:
        return Response("No audio received", status=400)

    mime = "audio/wav" if fname.lower().endswith(".wav") else "audio/mp3"
    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime)

    prompt = (
        "The audio contains a spoken question from a visually impaired user. "
        "If the audio has NO clear speech (silence or noise only), do NOT invent "
        "a question; reply exactly with: " + NO_SPEECH_MSG + "\n"
        "Otherwise answer clearly and briefly in 1-3 spoken sentences. "
        "Return ONLY the answer text."
    )
    answer = gemini([prompt, audio_part])
    if not answer:
        answer = NO_SPEECH_MSG
    print("[/ask] ->", answer[:80].replace("\n", " "), "...")
    return audio_reply(answer, request.form.get("voice", "Aria"))


# ---------- PCM endpoints for the ESP32-WROOM brain (Bluetooth A2DP out) ----------
# The ESP32 brain downloads the whole answer (WiFi), then plays it over
# Bluetooth (they can't run at once). We send 8kHz MONO to keep the buffer
# small enough for the WROOM's limited RAM (~129KB free); the ESP32 upsamples
# it to 44.1kHz stereo for the earbuds. 8kHz = telephone quality, fine for speech.
PCM_RATE = 8000


def text_to_pcm(text, voice="Aria"):
    """TTS the text, then decode the mp3 to raw 44100 Hz 16-bit MONO PCM bytes."""
    text_to_mp3(text, voice, "reply.mp3")
    data = open("reply.mp3", "rb").read()
    dec = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16,
                           nchannels=1, sample_rate=PCM_RATE)
    return bytes(dec.samples)


def pcm_response(text, voice="Aria"):
    pcm = text_to_pcm(text, voice)
    resp = Response(pcm, mimetype="application/octet-stream")
    resp.headers["X-SPES-Text"] = urllib.parse.quote(text)
    return resp


@app.route("/say")
def say_pcm():
    """GET /say?text=...  -> 44100 Hz mono PCM of that text. For testing the
    ESP32 A2DP playback without the mic."""
    text = (request.args.get("text") or "Hello from SPES").strip()
    print("[/say]", text[:60])
    return pcm_response(text, request.args.get("voice", "Aria"))


@app.route("/ask_pcm", methods=["POST"])
def ask_pcm():
    """Voice question in (raw WAV body) -> spoken answer as 44100 Hz mono PCM.
    Same as /ask but returns PCM the ESP32 can stream straight to the earbuds."""
    if "audio" in request.files:
        audio_bytes = request.files["audio"].read()
        fname = request.files["audio"].filename or "audio.wav"
    else:
        audio_bytes = request.data
        fname = "audio.wav"
    if not audio_bytes:
        return Response("No audio received", status=400)

    mime = "audio/wav" if fname.lower().endswith(".wav") else "audio/mp3"
    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime)
    prompt = (
        "The audio contains a spoken question from a visually impaired user. "
        "If the audio has NO clear speech (silence or noise only), do NOT invent "
        "a question; reply exactly with: " + NO_SPEECH_MSG + "\n"
        "Otherwise answer clearly and briefly in 1-3 spoken sentences. "
        "Return ONLY the answer text."
    )
    answer = gemini([prompt, audio_part]) or NO_SPEECH_MSG
    print("[/ask_pcm] ->", answer[:80].replace("\n", " "), "...")
    return pcm_response(answer, request.form.get("voice", "Aria"))


# ---------- /brain: full SPES driven by the ESP32-WROOM's INMP441 mic ----------
# The brain records voice and POSTs the raw WAV here. We reuse combined_spes to
# decide READ vs ASK, do the camera OCR / AI answer, and PLAY it on THIS laptop
# (so the audio comes out of the laptop's speakers / paired earbuds).
import combined_spes as cspes


@app.route("/brain", methods=["POST"])
def brain():
    if "audio" in request.files:
        audio_bytes = request.files["audio"].read()
    else:
        audio_bytes = request.data
    if not audio_bytes:
        return Response("No audio received", status=400)

    # Hand the audio to combined_spes' classifier (it reads QUESTION_WAV).
    with open(cspes.QUESTION_WAV, "wb") as f:
        f.write(audio_bytes)
    try:
        mode, heard, answer = cspes.classify_and_answer()
    except Exception as e:
        print("[/brain] classify error:", e)
        return Response("error", status=500)

    print("[/brain] heard:", heard, "| mode:", mode)
    if mode == "READ":
        text = cspes.read_camera_image()      # pulls from ESP32-CAM + OCR
        spoken = "Here is what I can read. " + text
    else:
        spoken = answer or NO_SPEECH_MSG

    print("[/brain] speaking:", spoken[:80])
    cspes.speak(spoken)                        # plays on the LAPTOP

    resp = Response("OK", status=200)
    resp.headers["X-SPES-Text"] = urllib.parse.quote(spoken)
    return resp


if __name__ == "__main__":
    print("Starting SPES backend server on http://0.0.0.0:5000 ...")
    app.run(host="0.0.0.0", port=5000)
