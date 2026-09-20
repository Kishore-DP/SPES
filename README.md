# SPES — See, Process, Enhance, Speak

An AI + IoT hands-free voice assistant (originally an assistive reading aid for the
visually impaired). You speak; SPES decides whether to **answer a question** or
**read text in front of you** (camera OCR), then speaks the result aloud.

> Hardware target: ESP32-CAM (eye) + ESP32-WROOM-32D (brain) + Bluetooth earbuds,
> with a laptop backend. This repo is the **software brain**. During development the
> laptop's own camera and microphone stand in for the ESP32 hardware.

## What's here

| File | Purpose |
|------|---------|
| `combined_spes.py` | Main flow: voice command → decide READ vs ASK → OCR or answer → speak |
| `spes_jarvis.py` | Continuous hands-free wake-word loop (wake word: **"Max"**) with memory + control commands |
| `spes_livekit.py` | LiveKit real-time voice agent (STT + LLM + TTS) with wake/sleep, memory, and control commands |
| `spes_memory.py` | Persistent conversation memory across sessions |
| `server.py` | Flask backend (REST endpoints for the SPES web/companion app) |
| `test_page.png` | Sample image for OCR testing |
| `docs/` | Project write-ups and paper (PDF) |

## Voice interface

- **Wake word:** "Max" (`spes_jarvis.py`), or "Max wake up" / "Max go to sleep" (`spes_livekit.py`)
- **Read mode:** "Max, read this" → camera capture → OCR → spoken text
- **Ask mode:** "Max, what is …?" → AI answer, spoken
- **Control:** "forget everything", "repeat that", "stop listening" (and louder/softer in `spes_jarvis.py`)

## Stack

- **AI / OCR:** Google Gemini (`google-genai`)
- **TTS:** edge-tts (offline stack) · Cartesia (LiveKit stack)
- **STT:** Gemini audio (offline) · Deepgram (LiveKit stack)
- **Real-time voice:** LiveKit Agents 1.8
- **Audio / vision:** sounddevice, OpenCV, playsound3

## Setup

1. `pip install google-genai pillow edge-tts playsound3 sounddevice opencv-python numpy flask flask-cors`
   (plus `livekit-agents`, `python-dotenv` for the LiveKit agent)
2. Create `key.txt` containing **only** your Gemini API key.
3. For LiveKit, create `.env` with `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `GOOGLE_API_KEY`.
4. Run:
   - `python combined_spes.py` — one voice command
   - `python spes_jarvis.py` — continuous hands-free ("Max, …")
   - `python spes_livekit.py console` — LiveKit real-time agent

> `key.txt`, `.env`, and `spes_memory.json` are gitignored — keep your keys local.
