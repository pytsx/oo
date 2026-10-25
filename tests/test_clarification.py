import asyncio

from news_machine.domain.models import Claim, ClaimKind, SourceRef, Story
from news_machine.infrastructure.repositories import SQLiteRepository
from news_machine.services.clarification import FactClarifier
from news_machine.services.ollama import OllamaUnderstanding


def test_fallback_promotes_numeric_facts_to_news_candidate():
    events = [
        {"id": "speech_1", "text": "Em 2025, 23,8% da população vive de aluguel.", "speaker_id": "speaker_01"},
        {"id": "speech_2", "text": "Em 2016 eram 18,9 milhões de lares.", "speaker_id": "speaker_01"},
    ]
    result = asyncio.run(OllamaUnderstanding("", "", False).analyze(events))
    assert result.news_candidate is not None
    assert result.news_candidate["relevance"] == "high"
    assert len(result.claims) == 2


def test_clarifier_reprocesses_persisted_numeric_claims(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "clarify.db"))
    for index, text in enumerate((
        "Em 2025, 23,8% da população vive de aluguel.",
        "A proporção de moradias próprias caiu para 60,2%.",
    )):
        repo.save(Claim(id=f"claim_{index}", statement=text, speaker_id="speaker_01",
            kind=ClaimKind.FACTUAL, source_event_ids=[f"speech_{index}"],
            source=SourceRef(source_id="live_housing")))
    stories = FactClarifier(repo).clarify("live_housing")
    assert stories
    assert repo.list(Story, "live_housing")
    assert all(story.requires_external_verification for story in stories)
