import json
import re
from collections import Counter

import httpx

from news_machine.domain.models import UnderstandingResult


SYSTEM_PROMPT = """Voce estrutura discurso jornalistico sem inventar informacao.
Responda somente JSON com: topic, summary, claims e news_candidate.
Cada claim tem statement, speaker_id, kind (factual|opinion|prediction|question|attribution),
source_event_ids e entities. news_candidate, quando houver, tem topic, summary, relevance
(low|medium|high) e requires_external_verification. Preserve a rastreabilidade."""


class OllamaUnderstanding:
    def __init__(self, base_url: str, model: str, enabled: bool = True):
        self.base_url, self.model, self.enabled = base_url.rstrip("/"), model, enabled

    async def analyze(self, events: list[dict], context: dict | None = None) -> UnderstandingResult:
        if not self.enabled:
            return self._fallback(events)
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"previous_context": context, "events": events}, ensure_ascii=False)},
            ],
            "options": {"temperature": 0.1},
        }
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
            return UnderstandingResult.model_validate_json(response.json()["message"]["content"])
        except (httpx.HTTPError, KeyError, ValueError):
            return self._fallback(events)

    @staticmethod
    def _fallback(events: list[dict]) -> UnderstandingResult:
        text = " ".join(e["text"] for e in events).strip()
        claims = []
        numeric = 0
        for event in events:
            statement = event["text"].strip()
            lower = statement.lower()
            question = statement.endswith("?") or any(x in lower for x in ("acha que", "será que", "sera que", "como que", "por que"))
            opinion = any(x in lower for x in ("eu acho", "acredito", "na minha opinião", "impressionante"))
            kind = "question" if question else ("opinion" if opinion else "factual")
            has_data = bool(re.search(r"(?:\b\d+[\d.,]*\s*%|R\$\s*\d|\b(?:19|20)\d{2}\b|\b\d+[\d.,]*\s*(?:milhões|bilhões|mil|reais|metros))", statement, re.I))
            numeric += has_data
            entities = list(dict.fromkeys(re.findall(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ.-]+(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ.-]+){0,3}", statement)))
            claims.append({"statement": statement, "speaker_id": event.get("speaker_id", "speaker_unknown"),
                "kind": kind, "source_event_ids": [event["id"]], "entities": entities})
        stop = {"para", "como", "mais", "uma", "isso", "essa", "esse", "aqui", "gente", "agora", "porque", "muito", "pela", "pelo", "anos", "vocês"}
        words = Counter(word.lower() for word in re.findall(r"[A-Za-zÀ-ÿ]{4,}", text) if word.lower() not in stop)
        subjects = [word for word, _ in words.most_common(3)]
        topic = "Dados sobre " + ", ".join(subjects) if subjects else text[:80]
        factual = [item for item in claims if item["kind"] == "factual"]
        candidate = None
        if numeric or len(factual) >= 3:
            candidate = {"topic": topic, "summary": text[:700],
                "relevance": "high" if numeric >= 2 else "medium",
                "requires_external_verification": True}
        return UnderstandingResult(topic=topic or "Sem assunto", summary=text, claims=claims, news_candidate=candidate)

