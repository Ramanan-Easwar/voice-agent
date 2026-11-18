"""TrainerTwin voice interview coach.

Local mic -> Deepgram STT -> TurnDirector -> Groq LLM -> TagStripper
-> Cartesia TTS -> local speakers. Wear headphones: there is no echo
cancellation, so the agent would otherwise hear itself.
"""

import asyncio
import os

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from interview import Interview, TagStripper, TurnDirector
from latency_logger import LatencyLogger
from prompts import SYSTEM_PROMPT

load_dotenv(override=True)


async def main():
    transport = LocalAudioTransport(
        LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )

    stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])
    llm = GroqLLMService(
        api_key=os.environ["GROQ_API_KEY"],
        settings=GroqLLMService.Settings(model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")),
    )
    tts = CartesiaTTSService(
        api_key=os.environ["CARTESIA_API_KEY"],
        settings=CartesiaTTSService.Settings(voice=os.environ["CARTESIA_VOICE_ID"]),
    )

    # The context holds the conversation; the user aggregator turns transcripts into
    # user messages (using the VAD to know when a turn starts and stops), and the
    # assistant aggregator records what the bot actually said.
    context = LLMContext([{"role": "system", "content": SYSTEM_PROMPT}])

    # Turn-taking has two silence settings:
    # - VAD stop_secs: short silence after which smart-turn is asked "are they done?"
    # - smart-turn stop_secs: the longest silence we wait through when smart-turn says
    #   "not done yet" (thinking pauses, "um..."), before ending the turn anyway
    vad = SileroVADAnalyzer(params=VADParams(stop_secs=float(os.getenv("VAD_STOP_SECS", "0.4"))))
    smart_turn = LocalSmartTurnAnalyzerV3(
        params=SmartTurnParams(stop_secs=float(os.getenv("SMART_TURN_STOP_SECS", "5")))
    )
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad,
            user_turn_strategies=UserTurnStrategies(
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=smart_turn)]
            ),
        ),
    )

    interview = Interview()

    pipeline = Pipeline(
        [
            transport.input(), # emits the InputAudioRawFrames
            stt, # for TranscriptionFrames
            aggregators.user(), # user db
            TurnDirector(interview, context),
            llm, # LLMTextFrames
            TagStripper(interview),
            tts, # TTSAudioRawFrames
            transport.output(),
            aggregators.assistant(), # bot  db
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        observers=[LatencyLogger("latency.jsonl")],  # watches frames, writes one line per turn
    )

    @worker.event_handler("on_pipeline_started")
    async def on_pipeline_started(worker, frame):
        # Bot speaks first: TurnDirector adds the OPEN note (greeting + question 1)
        await worker.queue_frames([LLMRunFrame()])

    runner = WorkerRunner()
    await runner.add_workers(worker)
    logger.info("Listening... press Ctrl+C to stop")
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
