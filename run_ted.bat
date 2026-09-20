@echo off
REM Launches the SPES LiveKit voice agent in console mode.
REM PYTHONUTF8=1 avoids the Windows emoji/charmap crash in LiveKit's console UI.
set PYTHONUTF8=1
python spes_livekit.py console
