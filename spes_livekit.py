# spes_livekit.py — SPES voice agent on LiveKit
# Stage L5: conversation MEMORY + CONTROL COMMANDS
#
# Cascade pipeline:
#   STT  = deepgram/nova-3        (via LiveKit inference gateway)
#   LLM  = gemini-flash-lite      (your Google AI Studio key)
#   TTS  = cartesia/sonic-2       (via LiveKit inference gateway)
#
# Max has two states: ASLEEP (ignores everything) and AWAKE (responds to all).
#   "Max wake up"        -> start listening
#   "Max go to sleep"    -> stop listening
# Control commands (while awake):
#   "forget everything"  -> wipe Max's memory
#   "repeat that"        -> say the last answer again
#   "stop listening"     -> go to sleep
# Max also REMEMBERS the conversation across sessions (spes_memory.json).
#
# Run it (talk using your laptop mic/speakers):
#   python spes_livekit.py console

from dotenv import load_dotenv
load_dotenv()   # loads keys from .env

from livekit import agents
from livekit.agents import (AgentSession, Agent, WorkerOptions, cli,
                            inference, StopResponse)
from livekit.plugins import google, silero

# reuse everything we already built and tested
import combined_spes as spes      # gives us spes.client + spes.MODELS (Gemini)
import spes_memory as memory       # persistent memory across sessions

WAKE_PHRASES = ("wake up", "wakeup", "wake-up", "wake")
SLEEP_PHRASES = ("go to sleep", "goto sleep", "go to bed", "stop listening",
                 "good night", "goodnight", "goodbye", "bye bye")
FORGET_PHRASES = ("forget everything", "clear your memory", "clear memory",
                  "forget me", "wipe your memory")
REPEAT_PHRASES = ("repeat that", "say that again", "repeat", "what did you say")


def _matches(text: str, phrases) -> bool:
    t = (text or "").lower()
    return any(p in t for p in phrases)


class Max(Agent):
    def __init__(self, mem):
        base = (
            "You are Max, a friendly voice assistant for a visually impaired "
            "user (part of a device called SPES). Answer briefly and clearly "
            "in a natural spoken style, usually one to three sentences. "
            "When the user wakes you, greet them in one short sentence. "
            "When they tell you to go to sleep, say a short goodbye."
        )
        # Inject what Max remembers from past chats into the system prompt.
        context = memory.build_context(mem)
        if context:
            base = base + "\n\n" + context

        super().__init__(instructions=base)
        self.mem = mem
        self.awake = False       # start asleep
        self.last_answer = ""    # for the REPEAT command

    async def on_user_turn_completed(self, turn_ctx, new_message):
        text = new_message.text_content or ""

        # --- ASLEEP: only the wake phrase gets through ---
        if not self.awake:
            if _matches(text, WAKE_PHRASES):
                self.awake = True
                print(f"  >>> AWAKE (heard: {text!r})")
                return
            print(f"  (asleep, ignoring: {text!r})")
            raise StopResponse()

        # --- CONTROL COMMANDS (while awake) ---
        if _matches(text, FORGET_PHRASES):
            self.mem["summary"] = ""
            self.mem["history"] = []
            memory.save_mem(self.mem)
            print("  >>> FORGET (memory cleared)")
            await self.session.say("Okay, I have cleared my memory.")
            raise StopResponse()

        if _matches(text, REPEAT_PHRASES):
            print("  >>> REPEAT")
            await self.session.say(
                self.last_answer or "I have nothing to repeat yet.")
            raise StopResponse()

        # --- SLEEP ---
        if _matches(text, SLEEP_PHRASES):
            self.awake = False
            print(f"  >>> GOING TO SLEEP (heard: {text!r})")
            return   # let Max say a short goodbye, then it's asleep next turn

        print(f"  (awake, responding: {text!r})")


async def entrypoint(ctx: agents.JobContext):
    mem = memory.load_mem()
    print(f"[memory] {len(mem.get('history', []))} past turns loaded.")
    agent = Max(mem)

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=inference.STT(model="deepgram/nova-3"),
        llm=google.LLM(model="gemini-flash-lite-latest"),
        tts=inference.TTS(model="cartesia/sonic-2"),
    )

    # Record every finished exchange into persistent memory.
    _pending = {"user": None}

    @session.on("conversation_item_added")
    def _on_item(evt):
        item = evt.item
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None) or ""
        if not text:
            return
        if role == "user":
            _pending["user"] = text
        elif role == "assistant":
            agent.last_answer = text
            if _pending["user"]:
                try:
                    memory.remember_turn(mem, _pending["user"], text,
                                         spes.client, spes.MODELS)
                except Exception as e:
                    print("  (memory save error:", e, ")")
                _pending["user"] = None

    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(
        instructions="Introduce yourself in one sentence as Max, and tell the user "
        "to say 'Max wake up' when they want you to start listening."
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
