"""Writes latency.jsonl: one line per turn, all times in ms since the session started.

It's a Pipecat observer, so it watches frames moving through the pipeline
without being part of it. A "turn" is one user answer plus the bot reply
that actually played. Replies cut off before any audio played are not
turns the user experienced, so they don't get a line.

How each field is measured:
- user_stopped_ms: when the VAD decided the user stopped, minus the VAD's
  stop_secs (it only decides after that much silence), so it's when the
  user actually went quiet
- stt_final_ms: when the last final transcript of the turn arrived
- llm_first_token_ms: first LLM text token of the reply that played
- tts_first_audio_ms: when the output transport started playing the reply
  (BotStartedSpeakingFrame; doesn't include the sound card's own buffer)
- interrupted: the user started speaking while this reply was playing
"""

import json
import time

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver
from pipecat.processors.frame_processor import FrameDirection


class LatencyLogger(BaseObserver):
    def __init__(self, path="latency.jsonl"):
        super().__init__()
        self.path = path
        self.session_start = time.time()
        self.turn = 0
        self.reset()
        open(self.path, "w").close()  # start a fresh file every session

    def reset(self):
        self.user_stopped = None
        self.stt_final = None
        self.llm_first_token = None
        self.tts_first_audio = None
        self.bot_speaking = False
        self.interrupted = False

    def ms(self, t):
        return round((t - self.session_start) * 1000)

    async def on_push_frame(self, data):
        # Each frame is seen once per hop; only look at the first push. Some frames are
        # sent both upstream and downstream as two copies, so only look at downstream.
        if not data.first_push or data.direction != FrameDirection.DOWNSTREAM:
            return

        frame = data.frame
        now = time.time()

        if isinstance(frame, VADUserStoppedSpeakingFrame) and not self.bot_speaking:
            # The latest one wins: if the user carries on after a pause, it's replaced
            self.user_stopped = frame.timestamp - frame.stop_secs

        elif isinstance(frame, TranscriptionFrame) and not self.bot_speaking:
            self.stt_final = now

        elif isinstance(frame, LLMFullResponseStartFrame):
            # A new LLM reply starts; any earlier one was cancelled
            self.llm_first_token = None

        elif isinstance(frame, LLMTextFrame) and self.llm_first_token is None:
            self.llm_first_token = now

        elif isinstance(frame, BotStartedSpeakingFrame):
            self.tts_first_audio = now
            self.bot_speaking = True

        elif isinstance(frame, InterruptionFrame) and self.bot_speaking:
            self.interrupted = True

        elif isinstance(frame, BotStoppedSpeakingFrame):
            self.write_line()
            self.reset()

    def write_line(self):
        # The greeting has no user turn before it, so there's nothing to measure
        if self.user_stopped is None or self.stt_final is None or self.llm_first_token is None:
            return

        self.turn += 1
        line = {
            "turn": self.turn,
            "user_stopped_ms": self.ms(self.user_stopped),
            "stt_final_ms": self.ms(self.stt_final),
            "llm_first_token_ms": self.ms(self.llm_first_token),
            "tts_first_audio_ms": self.ms(self.tts_first_audio),
            "interrupted": self.interrupted,
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(line) + "\n")
