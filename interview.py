"""Interview state (question counter) and the two processors that use it.

Python decides where the interview is. The LLM only writes the words, and
on a normal turn tells us with [FOLLOWUP] or [NEXT] whether it asked a
follow-up or moved on.
"""

from loguru import logger

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from prompts import ADVANCE, ASK_QUESTION, DECIDE, GIVE_FEEDBACK, OPEN, QUESTIONS


class Interview:
    """Which question we're on and whether its follow-up has been used."""

    def __init__(self):
        self.started = False
        self.index = 0  # current question, 0-3
        self.followup_used = False
        self.waiting_for_tag = False  # True when the LLM decides follow-up vs next
        self.finished = False  # True once the closing feedback has started playing
        # What the current reply does: "followup", "next" or None. Only applied once the
        # reply has played to the end, so a reply the user cut off never counts.
        self.pending = None

    def next_step(self):
        """What comes after the current question: the next question, or the feedback."""
        if self.index + 1 < len(QUESTIONS):
            return ASK_QUESTION.format(question=QUESTIONS[self.index + 1])
        return GIVE_FEEDBACK

    def move_to_next_question(self):
        if self.index + 1 < len(QUESTIONS):
            self.index += 1
            self.followup_used = False
        else:
            self.finished = True

    def note_for_this_turn(self):
        """Called once per user turn. Returns the session note for the LLM."""
        self.pending = None  # forget anything left over from a reply that never played

        if not self.started:
            self.started = True
            logger.info("Q1 | OPEN")
            return OPEN.format(next_step=ASK_QUESTION.format(question=QUESTIONS[0]))

        if self.followup_used:
            logger.info(f"Q{self.index + 1} | ADVANCE")
            self.pending = "next"
            return ADVANCE.format(next_step=self.next_step())

        logger.info(f"Q{self.index + 1} | DECIDE")
        self.waiting_for_tag = True
        return DECIDE.format(next_step=self.next_step())

    def llm_asked_followup(self):
        logger.info(f"Q{self.index + 1} | LLM chose FOLLOWUP")
        self.waiting_for_tag = False
        self.pending = "followup"

    def llm_moved_on(self):
        logger.info(f"Q{self.index + 1} | LLM chose NEXT")
        self.waiting_for_tag = False
        self.pending = "next"

    def reply_cancelled(self):
        # The user cut in before the reply finished playing, so it doesn't count
        if self.pending:
            logger.info(f"Q{self.index + 1} | reply cut off, ignoring {self.pending}")
        self.pending = None

    def bot_finished_speaking(self):
        # The reply played to the end without being interrupted, so its decision counts
        if self.pending == "followup":
            self.followup_used = True
        elif self.pending == "next":
            self.move_to_next_question()
        self.pending = None


class TurnDirector(FrameProcessor):
    """Sits before the LLM. Adds this turn's session note, tells the Interview when the bot
    finishes speaking or gets interrupted, and ends the session after the goodbye."""

    def __init__(self, interview, context):
        super().__init__()
        self.interview = interview
        self.context = context
        self.note = None  # the session note currently in the context
        self.ended = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame) and not frame.speculation:
            self.replace_note(self.interview.note_for_this_turn())

        if isinstance(frame, InterruptionFrame):
            self.interview.reply_cancelled()

        if isinstance(frame, BotStoppedSpeakingFrame):
            self.interview.bot_finished_speaking()
            if self.interview.finished and not self.ended:
                # The goodbye has been spoken: close the session
                self.ended = True
                await self.push_frame(EndWorkerFrame(), FrameDirection.DOWNSTREAM)

        await self.push_frame(frame, direction)

    def replace_note(self, text):
        # Keep only one session note in the history: drop the old one, add the new one at the end
        messages = [m for m in self.context.messages if m is not self.note]
        self.note = {"role": "system", "content": text}
        messages.append(self.note)
        self.context.set_messages(messages)


class TagStripper(FrameProcessor):
    """Sits after the LLM. Reads and removes [FOLLOWUP] / [NEXT] so TTS never says them."""

    def __init__(self, interview):
        super().__init__()
        self.interview = interview
        self.checking = False  # True while we're looking for the tag at the start of a reply
        self.buffer = ""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self.checking = self.interview.waiting_for_tag
            self.buffer = ""

        elif isinstance(frame, LLMTextFrame) and self.checking:
            self.buffer += frame.text
            # Hold the text back until we can see the whole tag (or clearly there isn't one)
            if "]" not in self.buffer and len(self.buffer) < 12:
                return
            self.checking = False
            frame.text = self.strip_tag(self.buffer)
            if not frame.text:
                return

        elif isinstance(frame, LLMFullResponseEndFrame) and self.checking:
            # Reply ended while we were still holding text back: send what we have
            self.checking = False
            text = self.strip_tag(self.buffer)
            if text:
                await self.push_frame(LLMTextFrame(text), direction)

        await self.push_frame(frame, direction)

    def strip_tag(self, text):
        text = text.lstrip()
        if text.startswith("[NEXT]"):
            self.interview.llm_moved_on()
            return text[len("[NEXT]"):].lstrip()
        if text.startswith("[FOLLOWUP]"):
            self.interview.llm_asked_followup()
            return text[len("[FOLLOWUP]"):].lstrip()
        # No tag: treat it as a follow-up, so the next turn is forced to move on
        self.interview.llm_asked_followup()
        return text
