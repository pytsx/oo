import asyncio

from news_machine.cognition.discourse_graph import DiscoursePipeline
from news_machine.domain.models import Claim, Discourse, SpeechEvent
from news_machine.infrastructure.repositories import SQLiteRepository
from news_machine.services.ollama import OllamaUnderstanding


def test_incremental_pipeline_preserves_sources(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "test.db"))
    pipeline = DiscoursePipeline(repo, OllamaUnderstanding("", "", enabled=False))
    events = [
        SpeechEvent(id="speech_1", stream_id="live_1", start=1, end=3, text="A empresa anunciou dois bilhões.", speaker_id="speaker_01"),
        SpeechEvent(id="speech_2", stream_id="live_1", start=4, end=6, text="Esse valor é novo?", speaker_id="speaker_02"),
    ]
    result = asyncio.run(pipeline.run(events))
    assert result["discourse"].last_event_end == 6
    assert [claim.source_event_ids for claim in result["claims"]] == [["speech_1"], ["speech_2"]]
    assert repo.list(Discourse, "live_1")
    assert repo.list(Claim)[0].source.start == 1


def test_rejects_invalid_timestamps():
    try:
        SpeechEvent(stream_id="x", start=2, end=1, text="texto")
    except ValueError:
        return
    raise AssertionError("deveria rejeitar intervalo invalido")

