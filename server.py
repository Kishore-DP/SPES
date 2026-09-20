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


if __name__ == "__main__":
    print("Starting SPES backend server on http://0.0.0.0:5000 ...")
    app.run(host="0.0.0.0", port=5000)
