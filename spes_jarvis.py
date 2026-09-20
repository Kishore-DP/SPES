# spes_jarvis.py — SPES continuous wake-word loop (wake word: "Max")
#
# Runs forever: listens, and only acts when you say the wake word "Max".
#   "Max, read this page"       -> camera/OCR mode
#   "Max, what is ...?"         -> question mode
#   (anything without "Max")    -> ignored
#
# Recording is dynamic: it STARTS when you begin speaking and STOPS after
# about 2 seconds of silence (no fixed timer). Press Ctrl+C to stop.

import re
import wave
import asyncio
import numpy as np
import sounddevice as sd
import edge_tts
from playsound3 import playsound
from google.genai import types

# reuse everything we already built and tested
import combined_spes as spes
import spes_memory as memory

WAKE_WORD = "Max"

SAMPLE_RATE = 16000
FRAME_SEC = 0.1                 # process audio in 100 ms frames
START_THRESHOLD = 80            # loudness above this = speech (silence ~1)
SILENCE_HANG_SEC = 2.0          # stop after this much continuous silence
MAX_SECONDS = 15                # safety cap on one utterance
QUESTION_WAV = "question.wav"


# --- speaking with adjustable volume (for LOUDER / SOFTER commands) ---
VOLUME_STEPS = ["-50%", "-25%", "+0%", "+25%", "+50%", "+100%"]
_volume_idx = 2   # start at +0%


def say(text):
    """Speak text via edge-tts at the current volume level."""
    async def _gen():
        c = edge_tts.Communicate(text, "en-US-AriaNeural",
                                 volume=VOLUME_STEPS[_volume_idx])
        await c.save("reply.mp3")
    asyncio.run(_gen())
    playsound("reply.mp3")


def rms(a):
    return float(np.sqrt(np.mean(a.astype(np.float32) ** 2)))


def record_until_silence():
    """Wait for speech to start, then record until ~2 s of silence."""
    frame_len = int(FRAME_SEC * SAMPLE_RATE)
    silence_needed = int(SILENCE_HANG_SEC / FRAME_SEC)   # silent frames to stop
    max_frames = int(MAX_SECONDS / FRAME_SEC)

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
    stream.start()
    try:
        # 1) wait (idle) until sound begins
        while True:
            data, _ = stream.read(frame_len)
            block = data[:, 0]
            if rms(block) > START_THRESHOLD:
                frames = [block.copy()]
                break

        # 2) keep recording; stop after enough continuous silence
        silent_count = 0
        while len(frames) < max_frames:
            data, _ = stream.read(frame_len)
            block = data[:, 0]
            frames.append(block.copy())
            if rms(block) > START_THRESHOLD:
                silent_count = 0
            else:
                silent_count += 1
                if silent_count >= silence_needed:
                    break
    finally:
        stream.stop()
        stream.close()

    audio = np.concatenate(frames)
    with wave.open(QUESTION_WAV, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())


def classify(audio_path, context=""):
    """Ask Gemini: was the wake word said? which mode? (and answer if a question).
    `context` is Max's memory of past conversations, used to answer questions."""
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    part = types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav")
    memory_block = (context + "\n\n") if context else ""
    prompt = (
        memory_block +
        "This audio is directed at a voice assistant named Max (a reading aid for "
        "a visually impaired user). First decide if the user said the wake word "
        "'Max' at the START of the audio. BE GENEROUS about close mishearings "
        "(like 'Max', 'Mac', 'Mack', 'Maxx', 'Max's', 'Maks'), but the wake word "
        "must be at the beginning. Then classify the intent into ONE mode. "
        "Reply in EXACTLY this format, nothing else:\n"
        "WAKE: YES or NO\n"
        "MODE: CONTROL or READ or ASK\n"
        "COMMAND: <if MODE is CONTROL, one of STOP, RESTART, FORGET, REPEAT, "
        "LOUDER, SOFTER; otherwise blank>\n"
        "HEARD: <exactly what the user said, word for word>\n"
        "ANSWER: <if WAKE is YES and MODE is ASK, a brief 1-3 sentence spoken "
        "answer; otherwise leave blank>\n\n"
        "MODE CONTROL = the user is commanding the assistant ITSELF, not asking a "
        "question. Map to COMMAND:\n"
        "  STOP    = stop listening, stop, exit, quit, shut down, goodbye, sleep\n"
        "  RESTART = restart, start over, reset yourself\n"
        "  FORGET  = forget everything, clear your memory, forget me\n"
        "  REPEAT  = say that again, repeat, what did you say\n"
        "  LOUDER  = speak louder / increase volume\n"
        "  SOFTER  = speak softer / lower volume\n"
        "MODE READ = the user wants to read/see something in front of them "
        "(e.g. 'read this', 'read the page', 'what does this say', "
        "'what is in front of me').\n"
        "MODE ASK = a general question for information.\n"
        "Commands about the assistant itself are ALWAYS CONTROL, never READ. "
        "If WAKE is NO, set MODE: ASK and leave the rest blank."
    )
    reply = spes.gemini([prompt, part])

    wake, mode, command, heard, answer = "NO", "ASK", "", "", ""
    for line in reply.splitlines():
        u = line.upper()
        if u.startswith("WAKE:"):
            wake = "YES" if "YES" in u else "NO"
        elif u.startswith("MODE:"):
            if "CONTROL" in u:
                mode = "CONTROL"
            elif "READ" in u:
                mode = "READ"
            else:
                mode = "ASK"
        elif u.startswith("COMMAND:"):
            command = line.split(":", 1)[1].strip().upper()
        elif u.startswith("HEARD:"):
            heard = line.split(":", 1)[1].strip()
        elif u.startswith("ANSWER:"):
            answer = line.split(":", 1)[1].strip()

    # Local backup: accept "Max" (and close mishearings) but ONLY as the first
    # word, so other words don't falsely trigger it.
    words = re.findall(r"[a-z']+", heard.lower())
    if words and words[0] in ("max", "maxx", "mac", "mack", "max's", "maks", "macks"):
        wake = "YES"

    # Local backup for control commands, in case the model misroutes them.
    low = heard.lower()
    control_map = [
        ("STOP", ["stop listening", "stop now", "shut down", "shutdown",
                  "exit", "quit", "goodbye", "good bye", "go to sleep"]),
        ("RESTART", ["restart", "start over", "reset yourself", "reboot"]),
        ("FORGET", ["forget everything", "clear your memory", "clear memory",
                    "forget me", "wipe your memory"]),
        ("REPEAT", ["say that again", "repeat that", "repeat", "what did you say"]),
        ("LOUDER", ["speak louder", "louder", "volume up"]),
        ("SOFTER", ["speak softer", "speak quieter", "quieter", "volume down"]),
    ]
    for cmd, phrases in control_map:
        if any(p in low for p in phrases):
            mode, command = "CONTROL", cmd
            break

    return wake, mode, command, heard, answer


def handle_control(command, last_answer, mem):
    """Run a CONTROL command. Returns (should_stop, new_last_answer)."""
    global _volume_idx

    if command == "STOP":
        say("Okay, stopping now. Goodbye!")
        return True, last_answer

    if command == "RESTART":
        say("Restarting.")
        print("  (restarting listener)")
        return False, last_answer

    if command == "FORGET":
        mem["summary"] = ""
        mem["history"] = []
        memory.save_mem(mem)
        say("I have cleared my memory.")
        return False, last_answer

    if command == "REPEAT":
        if last_answer:
            say(last_answer)
        else:
            say("I have nothing to repeat yet.")
        return False, last_answer

    if command == "LOUDER":
        _volume_idx = min(_volume_idx + 1, len(VOLUME_STEPS) - 1)
        say("Okay, louder.")
        return False, last_answer

    if command == "SOFTER":
        _volume_idx = max(_volume_idx - 1, 0)
        say("Okay, softer.")
        return False, last_answer

    say("Sorry, I did not understand that command.")
    return False, last_answer


def main():
    mem = memory.load_mem()          # load what Max remembers from past chats
    turns = len(mem.get("history", []))
    last_answer = ""

    print("=" * 50)
    print(f"   SPES is listening.  Say:  '{WAKE_WORD}, ...'")
    print("   (starts on your voice, stops after a 2s pause)")
    print(f"   Memory: {turns} past turns remembered.")
    print("   Commands: 'stop listening', 'restart', 'forget everything',")
    print("             'repeat that', 'speak louder/softer'")
    print("   (Ctrl+C to stop)")
    print("=" * 50)

    while True:
        print("\n[ listening... ]")
        record_until_silence()
        print("[ heard something, thinking... ]")
        try:
            context = memory.build_context(mem)      # give Max its memory
            wake, mode, command, heard, answer = classify(QUESTION_WAV, context)
        except Exception as e:
            print("  error:", e)
            continue

        if heard:
            print(f"  heard : {heard}")

        if wake != "YES":
            print(f"  (no '{WAKE_WORD}' wake word - ignoring)")
            continue

        print(f"  >>> wake word detected!  mode: {mode}"
              + (f" ({command})" if command else ""))

        if mode == "CONTROL":
            should_stop, last_answer = handle_control(command, last_answer, mem)
            if should_stop:
                break
            continue

        if mode == "READ":
            text = spes.read_camera_image()
            print("  read  :", text[:80])
            spoken = "Here is what I can read. " + text
            say(spoken)
            last_answer = spoken
            memory.remember_turn(mem, heard, "Read aloud: " + text,
                                 spes.client, spes.MODELS)
        else:
            if not answer:
                answer = "Sorry, I could not catch that. Please try again."
            print("  answer:", answer)
            say(answer)
            last_answer = answer
            memory.remember_turn(mem, heard, answer, spes.client, spes.MODELS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSPES stopped. Bye!")
