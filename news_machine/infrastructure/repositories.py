import json
import sqlite3
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from news_machine.domain.models import Article, ArticleAudit, Claim, Discourse, Evidence, EvidenceVerdict, SpeakerProfile, SpeechEvent, Story, StoryAssessment, TranscriptRevision, Utterance

T = TypeVar("T", bound=BaseModel)


class SQLiteRepository:
    MODELS = {m.__name__: m for m in (SpeechEvent, TranscriptRevision, Utterance, SpeakerProfile, Claim, Discourse, Story, Evidence, EvidenceVerdict, StoryAssessment, Article, ArticleAudit)}

    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS objects (
              kind TEXT NOT NULL, id TEXT PRIMARY KEY, stream_id TEXT,
              payload TEXT NOT NULL, created_at TEXT NOT NULL
            )
        """)
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_objects_kind_stream ON objects(kind, stream_id)")
        self.connection.commit()

    def save(self, obj: T) -> T:
        payload = obj.model_dump_json()
        stream_id = getattr(obj, "stream_id", None)
        if stream_id is None and hasattr(obj, "story_id"):
            story = self.get(Story, obj.story_id)
            stream_id = story.stream_id if story else None
        if stream_id is None and hasattr(obj, "article_id"):
            article = self.get(Article, obj.article_id)
            story = self.get(Story, article.story_id) if article else None
            stream_id = story.stream_id if story else None
        if stream_id is None and hasattr(obj, "source"):
            stream_id = obj.source.source_id
        self.connection.execute(
            "INSERT OR REPLACE INTO objects(kind,id,stream_id,payload,created_at) VALUES(?,?,?,?,?)",
            (obj.__class__.__name__, obj.id, stream_id, payload, obj.created_at.isoformat()),
        )
        self.connection.commit()
        return obj

    def get(self, model: type[T], object_id: str) -> T | None:
        row = self.connection.execute("SELECT payload FROM objects WHERE kind=? AND id=?", (model.__name__, object_id)).fetchone()
        return model.model_validate_json(row[0]) if row else None

    def list(self, model: type[T], stream_id: str | None = None) -> list[T]:
        sql, args = "SELECT payload FROM objects WHERE kind=?", [model.__name__]
        if stream_id is not None:
            sql += " AND stream_id=?"
            args.append(stream_id)
        sql += " ORDER BY created_at"
        return [model.model_validate_json(row[0]) for row in self.connection.execute(sql, args)]

    def close(self) -> None:
        self.connection.close()

