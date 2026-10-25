from dataclasses import dataclass
import os


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: str = os.getenv("NEWS_DB_PATH", "news_machine.db")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    ollama_enabled: bool = os.getenv("OLLAMA_ENABLED", "true").lower() in {"1", "true", "yes"}
    searxng_base_url: str = os.getenv("SEARXNG_BASE_URL", "")
    media_dir: str = os.getenv("NEWS_MEDIA_DIR", "media")

