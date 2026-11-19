"""Writes transcript.json: who said what, in order, with interrupted bot turns marked.

bot.py feeds it from the context aggregators' events, so the text is exactly
what went into the conversation history: the user's final transcript, and
only the words the bot actually spoke before any interruption.
"""

import json
import time


class TranscriptLogger:
    def __init__(self, path="transcript.json"):
        self.path = path
        self.session_start = time.time()
        self.lines = []
        self.save()  # start a fresh file every session

    def add(self, speaker, text, interrupted=False):
        self.lines.append(
            {
                "ms": round((time.time() - self.session_start) * 1000),
                "speaker": speaker,
                "text": text,
                "interrupted": interrupted,
            }
        )
        self.save()

    def save(self):
        # Rewritten after every line, so the file is complete even if the bot crashes
        with open(self.path, "w") as f:
            json.dump(self.lines, f, indent=2)
