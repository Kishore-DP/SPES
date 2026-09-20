# combined_spes.py — SPES: one voice flow that DECIDES read-mode vs ask-mode
#
# You speak. SPES decides:
#   - "read this / read the page / what's in front of me"  -> OCR the camera image
#   - anything else (a question)                           -> answer it
# Then it speaks the result.

import asyncio
import logging
import time
import wave
import numpy as np
import sounddevice as sd
import cv2
from google import genai
from google.genai import types
import edge_tts
from playsound3 import playsound

# Silence the harmless "Direct use of automatic function calling (AFC)..."
# notice that the google-genai SDK logs on every call.
logging.getLogger("google_genai").setLevel(logging.ERROR)
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

# --- Settings ---
RECORD_SECONDS = 5
SAMPLE_RATE = 16000
QUESTION_WAV = "question.wav"
SILENCE_THRESHOLD = 60
NO_SPEECH_MSG = "Sorry, I could not catch that. Please try again."

# For now we use the LAPTOP webcam as the camera.
# LATER: the ESP32-CAM photo will replace this capture step.
CAMERA_IMAGE = "capture.jpg"

with open("key.txt") as f:
    API_KEY = f.read().strip()
client = genai.Client(api_key=API_KEY)

MODELS = [
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
]


def gemini(contents):
    for model in MODELS:
        for attempt in range(3):
            try:
                return client.models.generate_content(
                    model=model, contents=contents
                ).text.strip()
            except Exception as e:
                msg = str(e)
                if "503" in msg or "UNAVAILABLE" in msg or "429" in msg:
                    time.sleep(3)
                    continue
                else:
                    break
    raise RuntimeError("All models busy")


def record_question():
    print(f"\n>>> Speak now ({RECORD_SECONDS} seconds)... <<<")
    audio = sd.rec(int(RECORD_SECONDS * SAMPLE_RATE),
                   samplerate=SAMPLE_RATE, channels=1, dtype="int16")
    sd.wait()
    with wave.open(QUESTION_WAV, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    loudness = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
    print(f"(mic level: {loudness:.0f})")
    return loudness


def classify_and_answer():
    """Send the voice to Gemini. It returns the mode, what it heard,
    and (for questions) an answer."""
    with open(QUESTION_WAV, "rb") as f:
        audio_bytes = f.read()
    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav")

    prompt = (
        "The audio is a spoken command from a visually impaired user of a device "
        "that has a camera. Decide the user's intent and reply in EXACTLY this "
        "format, nothing else:\n"
        "MODE: READ or ASK\n"
        "HEARD: <what the user said>\n"
        "ANSWER: <if MODE is ASK, a brief 1-3 sentence spoken answer; if MODE is "
        "READ, leave blank>\n\n"
        "Use MODE: READ when the user wants to read/see something in front of them "
        "(e.g. 'read this', 'read the page', 'what does this say', "
        "'what is in front of me'). Use MODE: ASK for general questions."
    )
    reply = gemini([prompt, audio_part])

    mode, heard, answer = "ASK", "", ""
    for line in reply.splitlines():
        u = line.upper()
        if u.startswith("MODE:"):
            mode = "READ" if "READ" in u else "ASK"
        elif u.startswith("HEARD:"):
            heard = line.split(":", 1)[1].strip()
        elif u.startswith("ANSWER:"):
            answer = line.split(":", 1)[1].strip()
    return mode, heard, answer


def capture_from_webcam():
    """Show a LIVE preview so you can aim at the text, then press SPACE to
    capture (ESC to cancel). Saves the photo to CAMERA_IMAGE.
    Returns True if a photo was captured, False if cancelled.
    LATER: this whole function is replaced by the ESP32-CAM sending a photo."""
    print("Opening camera preview...")
    print(">>> Point the camera at the text, then press SPACE to capture (ESC = cancel) <<<")
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    if not cap.isOpened():
        raise RuntimeError("Could not open the laptop camera.")

    window = "SPES Camera  -  SPACE = capture   ESC = cancel"
    captured = None
    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        preview = frame.copy()
        cv2.putText(preview, "SPACE = capture    ESC = cancel", (12, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.imshow(window, preview)
        key = cv2.waitKey(1) & 0xFF
        if key == 32:            # SPACE -> capture
            captured = frame
            break
        elif key == 27:          # ESC -> cancel
            break

    cap.release()
    cv2.destroyAllWindows()
    cv2.waitKey(1)               # let the window actually close on Windows

    if captured is None:
        print("Capture cancelled.")
        return False
    cv2.imwrite(CAMERA_IMAGE, captured)
    print(f"Photo captured ({captured.shape[1]}x{captured.shape[0]}).")
    return True


def read_camera_image():
    """Show preview, capture on SPACE, then OCR the photo."""
    if not capture_from_webcam():
        return "Capture cancelled. Nothing to read."
    with open(CAMERA_IMAGE, "rb") as f:
        img_bytes = f.read()
    img_part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
    prompt = (
        "Read all the text in this image and return it exactly as written. "
        "Only return the text, nothing else. If there is no readable text, "
        "reply: I could not find any text to read."
    )
    return gemini([prompt, img_part])


async def make_speech(words, filename="reply.mp3"):
    communicate = edge_tts.Communicate(words, "en-US-AriaNeural")
    await communicate.save(filename)


def speak(text):
    print("Converting to speech...")
    asyncio.run(make_speech(text))
    print("Playing audio... (put in your earbuds!)")
    playsound("reply.mp3")


def main():
    loudness = record_question()
    if loudness < SILENCE_THRESHOLD:
        print("No speech detected.")
        speak(NO_SPEECH_MSG)
        print("Done!")
        return

    print("Thinking...")
    mode, heard, answer = classify_and_answer()
    if heard:
        print(f"SPES heard : {heard}")
    print(f"Mode       : {mode}")

    if mode == "READ":
        print("Reading what the camera sees...")
        text = read_camera_image()
        print(f"Text read  : {text[:80]}...")
        speak("Here is what I can read. " + text)
    else:
        if not answer:
            answer = NO_SPEECH_MSG
        print(f"SPES answer: {answer}")
        speak(answer)

    print("Done!")


if __name__ == "__main__":
    main()
