"""TrainerTwin voice interview coach.

Step 1: the bare cascaded pipeline. Local mic -> Deepgram STT -> Groq LLM
-> Cartesia TTS -> local speakers. Wear headphones: there is no echo
cancellation, so the agent would otherwise hear itself.
"""

import asyncio
import os

from dotenv import load_dotenv
from loguru import logger

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
from pipecat.workers.runner import WorkerRunner

load_dotenv(override=True)

SYSTEM_PROMPT = (
    "You are a friendly assistant in a voice conversation. "
    "Your replies are spoken aloud, so keep them to one or two short sentences "
    "and never use lists, markdown or emojis."
)


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
    vad = SileroVADAnalyzer(params=VADParams(stop_secs=float(os.getenv("VAD_STOP_SECS", "0.2"))))
    aggregators = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=vad)
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True))

    @worker.event_handler("on_pipeline_started")
    async def on_pipeline_started(worker, frame):
        # Bot speaks first: ask the LLM for an opening line
        context.add_message({"role": "user", "content": "Say a short hello."})
        await worker.queue_frames([LLMRunFrame()])

    runner = WorkerRunner()
    await runner.add_workers(worker)
    logger.info("Listening... press Ctrl+C to stop")
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
