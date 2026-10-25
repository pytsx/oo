import json

import httpx
from pydantic import BaseModel

from news_machine.domain.models import ArticleCitation, Claim, Evidence, Story


class DraftPayload(BaseModel):
    headline: str
    lead: str
    body: str
    citations: list[ArticleCitation]


WRITE_PROMPT = """Redija uma noticia factual em portugues usando SOMENTE o dossie fornecido.
Nao acrescente numeros, nomes, datas ou causalidade ausentes. Diferencie fato corroborado de declaracao.
Responda somente JSON: headline, lead, body e citations. Cada citation deve conter claim_id e
evidence_ids existentes no dossie. Todo claim usado precisa de citacao."""


class OllamaArticleWriter:
    def __init__(self, base_url: str, model: str, enabled: bool = True):
        self.base_url, self.model, self.enabled = base_url.rstrip("/"), model, enabled

    async def write(self, story: Story, claims: list[Claim], evidence: list[Evidence]) -> DraftPayload:
        if not self.enabled:
            return self._fallback(story, claims, evidence)
        dossier = {
            "story": {"topic": story.topic, "summary": story.summary},
            "claims": [{"id": x.id, "statement": x.statement, "speaker_id": x.speaker_id} for x in claims],
            "evidence": [{"id": x.id, "claim_ids": x.claim_ids, "title": x.title,
                "excerpt": x.excerpt, "url": x.source.url, "stance": x.stance} for x in evidence],
        }
        payload = {"model": self.model, "stream": False, "format": "json",
            "messages": [{"role": "system", "content": WRITE_PROMPT},
                {"role": "user", "content": json.dumps(dossier, ensure_ascii=False)}],
            "options": {"temperature": 0.15}}
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
            return DraftPayload.model_validate_json(response.json()["message"]["content"])
        except (httpx.HTTPError, KeyError, ValueError):
            return self._fallback(story, claims, evidence)

    @staticmethod
    def _fallback(story: Story, claims: list[Claim], evidence: list[Evidence]) -> DraftPayload:
        citations = []
        paragraphs = []
        for claim in claims:
            ids = [x.id for x in evidence if claim.id in x.claim_ids and x.stance == "supports"]
            if ids:
                paragraphs.append(claim.statement.rstrip(".") + ".")
                citations.append(ArticleCitation(claim_id=claim.id, evidence_ids=ids))
        lead = paragraphs[0] if paragraphs else story.summary
        return DraftPayload(headline=story.topic, lead=lead, body="\n\n".join(paragraphs), citations=citations)

