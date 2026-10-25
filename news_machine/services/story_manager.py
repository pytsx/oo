import re
import unicodedata
from datetime import datetime, timezone

from news_machine.domain.models import Relevance, Story, StoryStatus


ACTIVE_STATUSES = {
    StoryStatus.DETECTED, StoryStatus.DEVELOPING, StoryStatus.INVESTIGATING,
    StoryStatus.WAITING, StoryStatus.CONSOLIDATING,
}
STOPWORDS = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "um", "uma",
    "para", "por", "com", "que", "se", "no", "na", "nos", "nas", "ao", "aos",
}


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return {token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) > 2 and token not in STOPWORDS}


class StoryManager:
    """Mantem uma historia por assunto ativo, sem depender da memoria do LLM."""

    def __init__(self, repository, similarity_threshold: float = 0.42):
        self.repository = repository
        self.similarity_threshold = similarity_threshold

    @staticmethod
    def similarity(left: str, right: str) -> float:
        a, b = _tokens(left), _tokens(right)
        return len(a & b) / len(a | b) if a and b else 0.0

    def find_match(self, stream_id: str, topic: str) -> Story | None:
        active = [story for story in self.repository.list(Story, stream_id) if story.status in ACTIVE_STATUSES]
        ranked = [(self.similarity(topic, story.topic), story) for story in active]
        if not ranked:
            return None
        score, story = max(ranked, key=lambda item: item[0])
        return story if score >= self.similarity_threshold else None

    def associate(
        self, *, stream_id: str, topic: str, summary: str, relevance: Relevance,
        claim_ids: list[str], requires_external_verification: bool = True,
    ) -> tuple[Story, bool]:
        story = self.find_match(stream_id, topic)
        if story is None:
            return Story(stream_id=stream_id, topic=topic, summary=summary, relevance=relevance,
                claim_ids=list(dict.fromkeys(claim_ids)), requires_external_verification=requires_external_verification), True
        story.claim_ids = list(dict.fromkeys(story.claim_ids + claim_ids))
        story.summary = summary or story.summary
        story.relevance = max((story.relevance, relevance), key=lambda value: list(Relevance).index(value))
        story.status = StoryStatus.DEVELOPING
        story.requires_external_verification |= requires_external_verification
        story.updated_at = datetime.now(timezone.utc)
        return story, False

