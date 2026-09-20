# spes_memory.py — gives SPES/Ted a persistent memory across conversations.
#
# Stores everything in spes_memory.json:
#   - "summary": a running set of durable facts about the user
#   - "history": every conversation turn (time, what the user said, Ted's reply)
#
# Ted reads this memory before answering, and updates it after each turn.

import json
import os
import time

MEM_FILE = "spes_memory.json"
RECENT_TURNS = 12        # how many recent turns to give Ted verbatim
SUMMARY_EVERY = 6        # refresh the long-term summary every N turns


def load_mem():
    if os.path.exists(MEM_FILE):
        try:
            with open(MEM_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"summary": "", "history": []}


def save_mem(mem):
    try:
        with open(MEM_FILE, "w", encoding="utf-8") as f:
            json.dump(mem, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("  (could not save memory:", e, ")")


def build_context(mem):
    """Text block Ted sees before answering, so it remembers past details."""
    parts = []
    if mem.get("summary"):
        parts.append("WHAT YOU REMEMBER ABOUT THE USER (from past chats):\n"
                     + mem["summary"])
    recent = mem.get("history", [])[-RECENT_TURNS:]
    if recent:
        lines = [f"User: {h['user']}\nMax: {h['ted']}" for h in recent]
        parts.append("RECENT CONVERSATION:\n" + "\n".join(lines))
    if not parts:
        return ""
    return ("\n\n".join(parts) +
            "\n\nUse this memory to answer naturally and recall details the user "
            "told you earlier. If they ask what you remember, tell them.\n")


def refresh_summary(mem, client, models):
    """Distill recent turns into the long-term summary, so memory stays compact."""
    recent = mem["history"][-SUMMARY_EVERY * 2:]
    convo = "\n".join(f"User: {h['user']}\nMax: {h['ted']}" for h in recent)
    prompt = (
        "You maintain the long-term memory of a personal voice assistant named Max. "
        "Update the memory summary below. Keep durable, useful facts about the user "
        "(their name, preferences, people/places they mention, plans, and anything "
        "they asked you to remember). Drop trivia. Keep it under 200 words.\n\n"
        f"EXISTING SUMMARY:\n{mem.get('summary') or '(none yet)'}\n\n"
        f"NEW CONVERSATION:\n{convo}\n\nUPDATED SUMMARY:"
    )
    for m in models:
        try:
            mem["summary"] = client.models.generate_content(
                model=m, contents=prompt).text.strip()
            break
        except Exception:
            continue
    save_mem(mem)


def remember_turn(mem, user_text, ted_text, client, models):
    """Record one exchange and occasionally refresh the summary."""
    mem["history"].append({
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "user": user_text,
        "ted": ted_text,
    })
    save_mem(mem)
    if len(mem["history"]) % SUMMARY_EVERY == 0:
        refresh_summary(mem, client, models)
