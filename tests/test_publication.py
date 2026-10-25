import asyncio

import pytest

from news_machine.cognition.publication_graph import PublicationPipeline
from news_machine.domain.models import (
    Article, ArticleCitation, Claim, ClaimKind, Evidence, EvidenceStance, Relevance,
    SourceRef, Story, StoryStatus,
)
from news_machine.infrastructure.repositories import SQLiteRepository
from news_machine.services.writing import DraftPayload, OllamaArticleWriter


def _dossier(repo):
    claim = Claim(id="claim_pub", statement="A Empresa Alfa anunciou investimento de dois bilhões",
        speaker_id="speaker_01", kind=ClaimKind.FACTUAL, source_event_ids=["speech_1"],
        source=SourceRef(source_id="live"), entities=["Empresa Alfa"])
    story = Story(id="story_pub", stream_id="live", topic="Empresa Alfa anuncia investimento",
        summary="Anúncio de investimento", relevance=Relevance.HIGH, status=StoryStatus.CRYSTALLIZED,
        claim_ids=[claim.id], evidence_ids=["evidence_pub"])
    repo.save(claim); repo.save(story)
    evidence = Evidence(id="evidence_pub", story_id=story.id, claim_ids=[claim.id], title="Comunicado",
        excerpt=claim.statement, source=SourceRef(type="web", source_id="empresa.example", url="https://empresa.example/a"),
        publisher="empresa.example", content_hash="abc", stance=EvidenceStance.SUPPORTS, quality=.8)
    repo.save(evidence)
    return story


def test_publication_creates_audited_publishable_article(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "publication.db"))
    story = _dossier(repo)
    writer = OllamaArticleWriter("", "", False)
    result = asyncio.run(PublicationPipeline(repo, writer).run(story.id))
    assert result["article"].status == "publishable"
    assert result["audit"].approved is True
    assert result["story"].status == "publishable"
    assert repo.list(Article)[0].citations[0].evidence_ids == ["evidence_pub"]


class BadWriter:
    async def write(self, story, claims, evidence):
        return DraftPayload(headline="Manchete", lead="Lead", body="Texto sem lastro.",
            citations=[ArticleCitation(claim_id="claim_pub", evidence_ids=["evidence_invented"])])


def test_audit_rejects_invented_evidence(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "rejected.db"))
    story = _dossier(repo)
    result = asyncio.run(PublicationPipeline(repo, BadWriter()).run(story.id))
    assert result["article"].status == "review"
    assert result["audit"].approved is False
    assert "evidence_invented" in result["audit"].missing_evidence_ids


def test_publication_refuses_uncrystallized_story(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "blocked.db"))
    story = _dossier(repo)
    story.status = StoryStatus.WAITING
    repo.save(story)
    with pytest.raises(ValueError):
        asyncio.run(PublicationPipeline(repo, OllamaArticleWriter("", "", False)).run(story.id))
