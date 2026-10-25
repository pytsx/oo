from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from news_machine.domain.models import (
    Article, ArticleAudit, ArticleStatus, Claim, Evidence, EvidenceStance, Story, StoryStatus,
)


class PublicationState(TypedDict, total=False):
    story: Story
    claims: list[Claim]
    evidence: list[Evidence]
    article: Article
    audit: ArticleAudit


class PublicationPipeline:
    def __init__(self, repository, writer):
        self.repository, self.writer = repository, writer
        graph = StateGraph(PublicationState)
        graph.add_node("consolidate", self.consolidate)
        graph.add_node("write_article", self.write_article)
        graph.add_node("audit_article", self.audit_article)
        graph.add_node("persist_state", self.persist_state)
        graph.add_edge(START, "consolidate")
        graph.add_edge("consolidate", "write_article")
        graph.add_edge("write_article", "audit_article")
        graph.add_edge("audit_article", "persist_state")
        graph.add_edge("persist_state", END)
        self.graph = graph.compile()

    async def run(self, story_id: str) -> PublicationState:
        story = self.repository.get(Story, story_id)
        if story is None:
            raise LookupError(f"story inexistente: {story_id}")
        if story.status != StoryStatus.CRYSTALLIZED:
            raise ValueError("somente historias cristalizadas podem ser redigidas")
        return await self.graph.ainvoke({"story": story})

    async def consolidate(self, state: PublicationState):
        story = state["story"]
        claims = [x for claim_id in story.claim_ids if (x := self.repository.get(Claim, claim_id))]
        evidence = [x for evidence_id in story.evidence_ids if (x := self.repository.get(Evidence, evidence_id))]
        evidence = [x for x in evidence if x.stance == EvidenceStance.SUPPORTS]
        return {"claims": claims, "evidence": evidence}

    async def write_article(self, state: PublicationState):
        draft = await self.writer.write(state["story"], state["claims"], state["evidence"])
        previous = [x for x in self.repository.list(Article) if x.story_id == state["story"].id]
        article = Article(story_id=state["story"].id, version=len(previous) + 1,
            headline=draft.headline, lead=draft.lead, body=draft.body,
            claim_ids=[x.claim_id for x in draft.citations],
            evidence_ids=list(dict.fromkeys(eid for x in draft.citations for eid in x.evidence_ids)),
            citations=draft.citations)
        return {"article": article}

    async def audit_article(self, state: PublicationState):
        article, claims, evidence = state["article"], state["claims"], state["evidence"]
        claim_ids, evidence_by_id = {x.id for x in claims}, {x.id: x for x in evidence}
        cited_claims = {x.claim_id for x in article.citations}
        unsupported = sorted((set(article.claim_ids) - claim_ids) | (set(article.claim_ids) - cited_claims))
        missing = sorted({eid for citation in article.citations for eid in citation.evidence_ids
            if eid not in evidence_by_id or citation.claim_id not in evidence_by_id[eid].claim_ids})
        issues = []
        if not article.headline.strip() or not article.lead.strip() or not article.body.strip(): issues.append("Texto incompleto")
        if unsupported: issues.append("Claims sem suporte no dossie")
        if missing: issues.append("Citacoes ausentes ou incompatíveis")
        if set(article.claim_ids) != cited_claims: issues.append("Mapa de claims e citacoes divergente")
        approved = not issues and bool(article.claim_ids)
        article.status = ArticleStatus.PUBLISHABLE if approved else ArticleStatus.REVIEW
        audit = ArticleAudit(article_id=article.id, approved=approved,
            unsupported_claim_ids=unsupported, missing_evidence_ids=missing, issues=issues)
        return {"article": article, "audit": audit}

    async def persist_state(self, state: PublicationState):
        self.repository.save(state["article"])
        self.repository.save(state["audit"])
        story = state["story"]
        story.status = StoryStatus.PUBLISHABLE if state["audit"].approved else StoryStatus.DRAFTED
        self.repository.save(story)
        return {"story": story}

