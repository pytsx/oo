import json
import re

import httpx

from news_machine.domain.models import Claim, Evidence, EvidenceStance, EvidenceVerdict


VERIFY_PROMPT = """Compare uma afirmacao com uma evidencia jornalistica.
Nao use conhecimento externo. Responda somente JSON com stance (supports, contradicts ou context),
confidence entre 0 e 1, rationale curto e supporting_excerpt copiado da evidencia.
supports exige que a evidencia sustente o significado; mera sobreposicao de palavras e context."""


class OllamaEvidenceVerifier:
    def __init__(self, base_url: str, model: str, enabled: bool = True):
        self.base_url, self.model, self.enabled = base_url.rstrip("/"), model, enabled

    async def compare(self, claim: Claim, evidence: Evidence) -> EvidenceVerdict:
        if not self.enabled:
            return self._fallback(claim, evidence)
        payload = {
            "model": self.model, "stream": False, "format": "json",
            "messages": [
                {"role": "system", "content": VERIFY_PROMPT},
                {"role": "user", "content": json.dumps({"claim": claim.statement, "evidence": evidence.excerpt}, ensure_ascii=False)},
            ],
            "options": {"temperature": 0},
        }
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
            data = json.loads(response.json()["message"]["content"])
            return EvidenceVerdict(story_id=evidence.story_id, evidence_id=evidence.id, claim_id=claim.id, **data)
        except (httpx.HTTPError, KeyError, ValueError):
            return self._fallback(claim, evidence)

    @staticmethod
    def _fallback(claim: Claim, evidence: Evidence) -> EvidenceVerdict:
        words = lambda text: {x for x in re.findall(r"[a-zA-ZÀ-ÿ0-9]+", text.lower()) if len(x) > 3}
        overlap = len(words(claim.statement) & words(evidence.excerpt)) / max(1, len(words(claim.statement)))
        negative = any(x in evidence.excerpt.lower() for x in ("nega", "negou", "falso", "desmente", "não ocorreu", "nao ocorreu"))
        stance = EvidenceStance.CONTEXT
        if overlap >= 0.35:
            stance = EvidenceStance.CONTRADICTS if negative else EvidenceStance.SUPPORTS
        return EvidenceVerdict(story_id=evidence.story_id, evidence_id=evidence.id, claim_id=claim.id, stance=stance,
            confidence=min(0.75, round(overlap, 3)), rationale="Fallback lexical; requer revisão semântica.",
            supporting_excerpt=evidence.excerpt[:300])
