import hashlib
import re
from datetime import datetime
from typing import TypedDict
from urllib.parse import urlparse

from langgraph.graph import END, START, StateGraph

from news_machine.domain.models import (
    Claim, Evidence, EvidenceStance, EvidenceVerdict, MaturityAssessment, SourceRef, Story,
    StoryAssessment, StoryStatus,
)


class InvestigationState(TypedDict, total=False):
    story: Story
    claims: list[Claim]
    queries: list[tuple[str, str]]
    hits: list[tuple[str, object]]
    evidence: list[Evidence]
    verdicts: list[EvidenceVerdict]
    assessment: StoryAssessment


def _words(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-zA-ZÀ-ÿ0-9]+", text.lower()) if len(x) > 3}


class InvestigationPipeline:
    def __init__(self, repository, search, verifier=None):
        self.repository, self.search = repository, search
        if verifier is None:
            from news_machine.services.verification import OllamaEvidenceVerifier
            verifier = OllamaEvidenceVerifier("", "", False)
        self.verifier = verifier
        graph = StateGraph(InvestigationState)
        graph.add_node("plan_research", self.plan_research)
        graph.add_node("search_sources", self.search_sources)
        graph.add_node("retrieve_evidence", self.retrieve_evidence)
        graph.add_node("compare_claims", self.compare_claims)
        graph.add_node("assess_maturity", self.assess_maturity)
        graph.add_node("persist_state", self.persist_state)
        graph.add_edge(START, "plan_research")
        graph.add_edge("plan_research", "search_sources")
        graph.add_edge("search_sources", "retrieve_evidence")
        graph.add_edge("retrieve_evidence", "compare_claims")
        graph.add_edge("compare_claims", "assess_maturity")
        graph.add_edge("assess_maturity", "persist_state")
        graph.add_edge("persist_state", END)
        self.graph = graph.compile()

    async def run(self, story_id: str) -> InvestigationState:
        story = self.repository.get(Story, story_id)
        if story is None:
            raise LookupError(f"story inexistente: {story_id}")
        claims = [claim for claim_id in story.claim_ids if (claim := self.repository.get(Claim, claim_id))]
        story.status = StoryStatus.INVESTIGATING
        return await self.graph.ainvoke({"story": story, "claims": claims})

    async def plan_research(self, state: InvestigationState):
        queries = []
        for claim in state["claims"]:
            entities = " ".join(claim.entities[:3])
            queries.append((claim.id, f'{entities} "{claim.statement}"'.strip()))
        return {"queries": queries}

    async def search_sources(self, state: InvestigationState):
        hits = []
        for claim_id, query in state["queries"]:
            try:
                found = await self.search.search(query, limit=5)
            except Exception:
                found = []
            hits.extend((claim_id, hit) for hit in found)
        # A mesma URL e claim nao devem inflar a evidencia.
        unique = {(claim_id, hit.url): (claim_id, hit) for claim_id, hit in hits}
        return {"hits": list(unique.values())}

    async def retrieve_evidence(self, state: InvestigationState):
        evidence = []
        for claim_id, hit in state["hits"]:
            try:
                body = await self.search.retrieve(hit.url)
            except Exception:
                body = hit.snippet
            content = (body or hit.snippet).strip()
            if not content:
                continue
            domain = urlparse(hit.url).hostname or ""
            quality = 0.75 if hit.url.startswith("https://") else 0.55
            evidence.append(Evidence(
                story_id=state["story"].id, claim_ids=[claim_id], title=hit.title,
                excerpt=content[:1500], source=SourceRef(type="web", source_id=domain, url=hit.url),
                publisher=domain, content_hash=hashlib.sha256(content.encode()).hexdigest(), quality=quality,
            ))
        return {"evidence": evidence}

    async def compare_claims(self, state: InvestigationState):
        claims = {claim.id: claim for claim in state["claims"]}
        verdicts = []
        for item in state["evidence"]:
            claim = claims[item.claim_ids[0]]
            verdict = await self.verifier.compare(claim, item)
            verdicts.append(verdict)
            item.verdict_id = verdict.id
            item.stance = verdict.stance if verdict.confidence >= 0.55 else EvidenceStance.CONTEXT
        return {"evidence": state["evidence"], "verdicts": verdicts}

    async def assess_maturity(self, state: InvestigationState):
        story, claims, evidence = state["story"], state["claims"], state["evidence"]
        supported = {claim_id for item in evidence if item.stance == EvidenceStance.SUPPORTS for claim_id in item.claim_ids}
        contradicted = [item for item in evidence if item.stance == EvidenceStance.CONTRADICTS]
        relevant = [item for item in evidence if item.stance != EvidenceStance.CONTEXT]
        domains = {item.publisher for item in relevant if item.publisher}
        completeness = len(supported) / max(1, len(claims))
        stability = 0.7 if len(story.claim_ids) >= 2 else 0.45
        evidence_quality = sum(item.quality for item in relevant) / max(1, len(relevant))
        consistency = 1 - len(contradicted) / max(1, len(relevant))
        blockers = [f"Fonte contradiz: {item.title}" for item in contradicted]
        questions = []
        if len(domains) < 2: questions.append("Obter confirmacao de uma segunda fonte independente")
        if completeness < 0.8: questions.append("Corroborar claims ainda sem suporte")
        ready = completeness >= 0.8 and stability >= 0.65 and evidence_quality >= 0.65 and consistency >= 0.8 and len(domains) >= 2 and not blockers
        maturity = MaturityAssessment(
            completeness=round(completeness, 3), stability=stability,
            evidence_quality=round(evidence_quality, 3), consistency=round(consistency, 3),
            unresolved_questions=questions, blocking_contradictions=blockers,
            ready_for_crystallization=ready,
            reasoning="Critérios verificáveis atendidos." if ready else "Evidência insuficiente ou instável; manter investigação aberta.",
        )
        assessment = StoryAssessment(
            story_id=story.id, assessment=maturity,
            supporting_evidence_ids=[x.id for x in evidence if x.stance == EvidenceStance.SUPPORTS],
            contradicting_evidence_ids=[x.id for x in contradicted],
        )
        story.evidence_ids = list(dict.fromkeys(story.evidence_ids + [x.id for x in evidence]))
        story.maturity_assessment_id = assessment.id
        story.status = StoryStatus.CRYSTALLIZED if ready else StoryStatus.WAITING
        return {"story": story, "assessment": assessment}

    async def persist_state(self, state: InvestigationState):
        for evidence in state["evidence"]:
            self.repository.save(evidence)
        for verdict in state["verdicts"]:
            self.repository.save(verdict)
        self.repository.save(state["assessment"])
        self.repository.save(state["story"])
        return {}

