import re
from collections import Counter

from news_machine.domain.models import Claim, Relevance, Story
from news_machine.services.story_manager import StoryManager


DATA_PATTERN = re.compile(r"(?:\b\d+[\d.,]*\s*%|R\$\s*\d|\b(?:19|20)\d{2}\b|\b\d+[\d.,]*\s*(?:milhões|bilhões|mil|reais|metros))", re.I)


class FactClarifier:
    """Abre dossies verificaveis a partir de claims ja persistidos."""

    def __init__(self, repository):
        self.repository = repository
        self.story_manager = StoryManager(repository, similarity_threshold=0.30)

    def clarify(self, stream_id: str) -> list[Story]:
        claims = self.repository.list(Claim, stream_id)
        candidates = [claim for claim in claims if DATA_PATTERN.search(claim.statement)]
        if not candidates:
            candidates = [claim for claim in claims if claim.kind == "factual"]
        groups: list[list[Claim]] = []
        for claim in candidates:
            tokens = self._tokens(claim.statement)
            match = next((group for group in groups if self._similar(tokens, self._tokens(" ".join(x.statement for x in group))) >= .18), None)
            if match is None:
                groups.append([claim])
            else:
                match.append(claim)
        stories = []
        for group in groups:
            if not group:
                continue
            words = Counter(word for claim in group for word in self._tokens(claim.statement))
            subject = ", ".join(word for word, _ in words.most_common(3))
            story, _ = self.story_manager.associate(stream_id=stream_id,
                topic=f"Esclarecimento: {subject}", summary=" ".join(x.statement for x in group)[:900],
                relevance=Relevance.HIGH if len(group) >= 2 else Relevance.MEDIUM,
                claim_ids=[x.id for x in group], requires_external_verification=True)
            self.repository.save(story)
            stories.append(story)
        return stories

    @staticmethod
    def _tokens(text: str) -> set[str]:
        stop = {"para", "como", "mais", "uma", "isso", "essa", "esse", "aqui", "agora", "porque", "muito", "pela", "pelo"}
        return {x.lower() for x in re.findall(r"[A-Za-zÀ-ÿ]{4,}", text) if x.lower() not in stop}

    @staticmethod
    def _similar(a: set[str], b: set[str]) -> float:
        return len(a & b) / max(1, len(a | b))
