import asyncio

from news_machine.cognition.investigation_graph import InvestigationPipeline
from news_machine.domain.models import Claim, ClaimKind, SourceRef, Story, Relevance, Evidence, StoryAssessment
from news_machine.infrastructure.repositories import SQLiteRepository
from news_machine.services.search import SearchHit, SourceSearch


class FakeSearch(SourceSearch):
    async def search(self, query: str, limit: int = 5):
        if "dois bilhões" in query:
            return [
                SearchHit("Comunicado oficial", "https://empresa.example/anuncio", "Empresa Alfa anunciou investimento de dois bilhões"),
                SearchHit("Agência confirma valor", "https://agencia.example/noticia", "Empresa Alfa anunciou investimento de dois bilhões"),
            ]
        return [
            SearchHit("Detalhes do projeto", "https://empresa.example/projeto", "A infraestrutura será construída em São Paulo"),
            SearchHit("Projeto em São Paulo", "https://jornal.example/projeto", "A infraestrutura será construída em São Paulo"),
        ]

    async def retrieve(self, url: str):
        return {
            "https://empresa.example/anuncio": "A Empresa Alfa anunciou investimento de dois bilhões.",
            "https://agencia.example/noticia": "A Empresa Alfa anunciou investimento de dois bilhões.",
            "https://empresa.example/projeto": "A infraestrutura será construída em São Paulo.",
            "https://jornal.example/projeto": "A infraestrutura será construída em São Paulo.",
        }[url]


def _claim(statement, claim_id):
    return Claim(id=claim_id, statement=statement, speaker_id="speaker_01", kind=ClaimKind.FACTUAL,
        source_event_ids=["speech_1"], source=SourceRef(source_id="live"), entities=["Empresa Alfa"])


def test_investigation_crystallizes_only_with_independent_evidence(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "investigation.db"))
    claims = [
        _claim("A Empresa Alfa anunciou investimento de dois bilhões", "claim_1"),
        _claim("A infraestrutura será construída em São Paulo", "claim_2"),
    ]
    for claim in claims: repo.save(claim)
    story = Story(id="story_1", stream_id="live", topic="Investimento da Empresa Alfa",
        summary="Novo investimento", relevance=Relevance.HIGH, claim_ids=[x.id for x in claims])
    repo.save(story)

    result = asyncio.run(InvestigationPipeline(repo, FakeSearch()).run(story.id))

    assert result["story"].status == "crystallized"
    assert result["assessment"].assessment.ready_for_crystallization is True
    assert len(repo.list(Evidence, "live")) == 4
    assert len(repo.list(StoryAssessment, "live")) == 1


class EmptySearch(SourceSearch):
    async def search(self, query: str, limit: int = 5): return []
    async def retrieve(self, url: str): raise AssertionError("nao deve recuperar")


def test_investigation_waits_when_sources_are_missing(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "empty.db"))
    claim = _claim("A Empresa Alfa anunciou investimento", "claim_empty")
    repo.save(claim)
    story = Story(id="story_empty", stream_id="live", topic="Investimento", summary="Possível anúncio",
        relevance=Relevance.HIGH, claim_ids=[claim.id])
    repo.save(story)
    result = asyncio.run(InvestigationPipeline(repo, EmptySearch()).run(story.id))
    assert result["story"].status == "waiting"
    assert result["assessment"].assessment.ready_for_crystallization is False
    assert result["assessment"].assessment.unresolved_questions
