import asyncio
import threading
from typing import Any

from news_machine.cognition.discourse_graph import DiscoursePipeline
from news_machine.cognition.investigation_graph import InvestigationPipeline
from news_machine.cognition.publication_graph import PublicationPipeline
from news_machine.config import Settings
from news_machine.domain.models import SpeechEvent
from news_machine.infrastructure.event_bus import LocalEventBus
from news_machine.infrastructure.repositories import SQLiteRepository
from news_machine.services.ollama import OllamaUnderstanding
from news_machine.services.story_manager import StoryManager
from news_machine.services.search import SearxNGSearch
from news_machine.services.verification import OllamaEvidenceVerifier
from news_machine.services.writing import OllamaArticleWriter


STOP = object()


class EmbeddedNewsRuntime:
    """Ponte thread-safe entre o transcritor sincrono e o pipeline assincrono."""

    def __init__(self, settings: Settings, stream_id: str):
        self.settings, self.stream_id = settings, stream_id
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run_loop, name="news-pipeline", daemon=True)
        self.failure: BaseException | None = None
        self.thread.start()
        if not self.ready.wait(timeout=10):
            raise RuntimeError("pipeline nao iniciou")

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.bus = LocalEventBus()
        self.repository = SQLiteRepository(self.settings.db_path)
        manager = StoryManager(self.repository)
        self.pipeline = DiscoursePipeline(
            self.repository,
            OllamaUnderstanding(self.settings.ollama_base_url, self.settings.ollama_model, self.settings.ollama_enabled),
            manager,
        )
        verifier = OllamaEvidenceVerifier(self.settings.ollama_base_url, self.settings.ollama_model, self.settings.ollama_enabled)
        self.investigation = InvestigationPipeline(self.repository, SearxNGSearch(self.settings.searxng_base_url), verifier)
        self.publication = PublicationPipeline(self.repository,
            OllamaArticleWriter(self.settings.ollama_base_url, self.settings.ollama_model, self.settings.ollama_enabled))
        self.worker = self.loop.create_task(self._consume_speech())
        self.investigation_worker = self.loop.create_task(self._consume_stories())
        self.publication_worker = self.loop.create_task(self._consume_publication())
        self.ready.set()
        self.loop.run_forever()
        self.repository.close()
        self.loop.close()

    async def _consume_speech(self):
        try:
            stopping = False
            while not stopping:
                event = await self.bus.get("speech.confirmed")
                if event is STOP:
                    break
                batch = [event]
                while len(batch) < 8 and batch[-1].end - batch[0].start < 24:
                    try:
                        next_event = await asyncio.wait_for(self.bus.get("speech.confirmed"), timeout=2.0)
                    except asyncio.TimeoutError:
                        break
                    if next_event is STOP:
                        stopping = True
                        break
                    batch.append(next_event)
                result = await self.pipeline.run(batch)
                for story in result["stories"]:
                    await self.bus.publish("story.detected", story.id)
        except BaseException as exc:
            self.failure = exc

    async def _consume_stories(self):
        try:
            async for story_id in self.bus.subscribe("story.detected"):
                if story_id is STOP:
                    break
                result = await self.investigation.run(story_id)
                if result["story"].status == "crystallized":
                    await self.bus.publish("story.crystallized", story_id)
        except BaseException as exc:
            self.failure = exc

    async def _consume_publication(self):
        try:
            async for story_id in self.bus.subscribe("story.crystallized"):
                if story_id is STOP:
                    break
                await self.publication.run(story_id)
        except BaseException as exc:
            self.failure = exc

    def publish(self, raw: dict[str, Any], timeout: float = 5) -> None:
        event = SpeechEvent(stream_id=self.stream_id, **raw)
        # Espera apenas a entrada na fila, nunca a analise do evento.
        asyncio.run_coroutine_threadsafe(self.bus.publish("speech.confirmed", event), self.loop).result(timeout=timeout)

    def close(self, timeout: float = 130) -> None:
        asyncio.run_coroutine_threadsafe(self.bus.publish("speech.confirmed", STOP), self.loop).result(timeout=5)
        asyncio.run_coroutine_threadsafe(self._await(self.worker), self.loop).result(timeout=timeout)
        asyncio.run_coroutine_threadsafe(self.bus.publish("story.detected", STOP), self.loop).result(timeout=5)
        asyncio.run_coroutine_threadsafe(self._await(self.investigation_worker), self.loop).result(timeout=timeout)
        asyncio.run_coroutine_threadsafe(self.bus.publish("story.crystallized", STOP), self.loop).result(timeout=5)
        asyncio.run_coroutine_threadsafe(self._await(self.publication_worker), self.loop).result(timeout=timeout)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        if self.failure:
            raise RuntimeError("worker de compreensao falhou") from self.failure

    async def _await(self, task):
        await task

