from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class TraceableModel(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SourceRef(BaseModel):
    type: Literal["livestream", "vod", "document", "web"] = "livestream"
    source_id: str
    start: float | None = None
    end: float | None = None
    url: str | None = None


class WordToken(BaseModel):
    id: str
    text: str
    start: float
    end: float
    confidence: float | None = None
    timing_source: Literal["whisper", "estimated", "manual"] = "estimated"


class SpeechEvent(TraceableModel):
    id: str = Field(default_factory=lambda: new_id("speech"))
    stream_id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1)
    speaker_id: str = "speaker_unknown"
    confirmed_at: datetime | None = None
    superseded_by: str | None = None
    words: list[WordToken] = Field(default_factory=list)

    @model_validator(mode="after")
    def chronological(self):
        if self.end <= self.start:
            raise ValueError("end deve ser maior que start")
        self.text = self.text.strip()
        if not self.words and self.text:
            parts = self.text.split()
            duration = self.end - self.start
            self.words = [WordToken(id=f"{self.id}_w{index:03d}", text=word,
                start=self.start + duration * index / len(parts),
                end=self.start + duration * (index + 1) / len(parts), timing_source="estimated")
                for index, word in enumerate(parts)]
        return self


class Utterance(TraceableModel):
    id: str = Field(default_factory=lambda: new_id("utterance"))
    speaker_id: str
    text: str
    source_event_ids: list[str]
    source: SourceRef


class SpeakerProfile(TraceableModel):
    id: str = Field(default_factory=lambda: new_id("speaker_profile"))
    stream_id: str
    speaker_id: str
    display_name: str
    role: str | None = None
    notes: str | None = None
    cluster_ids: list[str] = Field(default_factory=list)
    sample_event_ids: list[str] = Field(default_factory=list)
    merged_into: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TranscriptRevision(TraceableModel):
    id: str = Field(default_factory=lambda: new_id("revision"))
    stream_id: str
    event_ids: list[str]
    operation: Literal["edit", "merge", "reprocess", "speaker_change", "word_edit", "split"]
    before_text: str
    after_text: str
    reason: str | None = None


class ClaimKind(str, Enum):
    FACTUAL = "factual"
    OPINION = "opinion"
    PREDICTION = "prediction"
    QUESTION = "question"
    ATTRIBUTION = "attribution"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    CORROBORATED = "corroborated"
    ATTRIBUTED = "attributed"
    UNVERIFIED = "unverified"
    CONTRADICTED = "contradicted"


class Claim(TraceableModel):
    id: str = Field(default_factory=lambda: new_id("claim"))
    statement: str
    speaker_id: str
    kind: ClaimKind
    source_event_ids: list[str]
    source: SourceRef
    entities: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus = VerificationStatus.PENDING
    evidence_ids: list[str] = Field(default_factory=list)


class Relevance(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StoryStatus(str, Enum):
    DETECTED = "detected"
    DEVELOPING = "developing"
    INVESTIGATING = "investigating"
    WAITING = "waiting"
    CONSOLIDATING = "consolidating"
    CRYSTALLIZED = "crystallized"
    DRAFTED = "drafted"
    VERIFIED = "verified"
    PUBLISHABLE = "publishable"


class Story(TraceableModel):
    id: str = Field(default_factory=lambda: new_id("story"))
    stream_id: str
    topic: str
    summary: str
    relevance: Relevance
    status: StoryStatus = StoryStatus.DETECTED
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    maturity_assessment_id: str | None = None
    requires_external_verification: bool = True
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Discourse(TraceableModel):
    id: str = Field(default_factory=lambda: new_id("discourse"))
    stream_id: str
    topic: str
    summary: str
    status: Literal["active", "closed"] = "active"
    speaker_ids: list[str] = Field(default_factory=list)
    utterance_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    story_ids: list[str] = Field(default_factory=list)
    last_event_end: float = 0


class UnderstandingResult(BaseModel):
    topic: str
    summary: str
    claims: list[dict] = Field(default_factory=list)
    news_candidate: dict | None = None


class MaturityAssessment(BaseModel):
    completeness: float = Field(ge=0, le=1)
    stability: float = Field(ge=0, le=1)
    evidence_quality: float = Field(ge=0, le=1)
    consistency: float = Field(ge=0, le=1)
    unresolved_questions: list[str] = Field(default_factory=list)
    blocking_contradictions: list[str] = Field(default_factory=list)
    ready_for_crystallization: bool = False
    reasoning: str


class EvidenceStance(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class Evidence(TraceableModel):
    id: str = Field(default_factory=lambda: new_id("evidence"))
    story_id: str
    claim_ids: list[str]
    title: str
    excerpt: str
    source: SourceRef
    publisher: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str
    stance: EvidenceStance = EvidenceStance.CONTEXT
    quality: float = Field(ge=0, le=1)
    verdict_id: str | None = None


class StoryAssessment(TraceableModel):
    id: str = Field(default_factory=lambda: new_id("assessment"))
    story_id: str
    assessment: MaturityAssessment
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)


class EvidenceVerdict(TraceableModel):
    id: str = Field(default_factory=lambda: new_id("verdict"))
    story_id: str
    evidence_id: str
    claim_id: str
    stance: EvidenceStance
    confidence: float = Field(ge=0, le=1)
    rationale: str
    supporting_excerpt: str


class ArticleStatus(str, Enum):
    DRAFTED = "drafted"
    REVIEW = "review"
    PUBLISHABLE = "publishable"


class ArticleCitation(BaseModel):
    claim_id: str
    evidence_ids: list[str]


class Article(TraceableModel):
    id: str = Field(default_factory=lambda: new_id("article"))
    story_id: str
    version: int = Field(default=1, ge=1)
    headline: str
    lead: str
    body: str
    claim_ids: list[str]
    evidence_ids: list[str]
    citations: list[ArticleCitation]
    status: ArticleStatus = ArticleStatus.DRAFTED


class ArticleAudit(TraceableModel):
    id: str = Field(default_factory=lambda: new_id("audit"))
    article_id: str
    approved: bool
    unsupported_claim_ids: list[str] = Field(default_factory=list)
    missing_evidence_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)

