import asyncio

from news_machine.cognition.discourse_graph import DiscoursePipeline
from news_machine.domain.models import SpeechEvent, Story, UnderstandingResult
from news_machine.infrastructure.repositories import SQLiteRepository


class CandidateUnderstanding:
    async def analyze(self, events, context=None):
        text = " ".join(event["text"] for event in events)
        return UnderstandingResult(
            topic="Investimento da Empresa Alfa em infraestrutura",
            summary=text,
            claims=[{
                "statement": text, "speaker_id": "speaker_01", "kind": "factual",
                "source_event_ids": [event["id"] for event in events], "entities": ["Empresa Alfa"],
            }],
            news_candidate={
                "topic": "Empresa Alfa anuncia investimento em infraestrutura",
                "summary": text, "relevance": "high", "requires_external_verification": True,
            },
        )


def test_recurring_topic_updates_existing_story(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "stories.db"))
    pipeline = DiscoursePipeline(repo, CandidateUnderstanding())
    first = SpeechEvent(id="speech_a", stream_id="live", start=0, end=2,
        text="A Empresa Alfa anunciou investimento.", speaker_id="speaker_01")
    second = SpeechEvent(id="speech_b", stream_id="live", start=30, end=33,
        text="O investimento em infraestrutura será de dois bilhões.", speaker_id="speaker_01")

    one = asyncio.run(pipeline.run([first]))
    two = asyncio.run(pipeline.run([second]))

    stories = repo.list(Story, "live")
    assert len(stories) == 1
    assert one["stories"][0].id == two["stories"][0].id
    assert len(stories[0].claim_ids) == 2
    assert stories[0].status == "developing"

