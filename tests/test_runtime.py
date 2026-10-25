from news_machine.config import Settings
from news_machine.domain.models import Discourse, SpeechEvent
from news_machine.infrastructure.repositories import SQLiteRepository
from news_machine.services.runtime import EmbeddedNewsRuntime


def test_embedded_runtime_drains_queue_before_closing(tmp_path):
    path = str(tmp_path / "runtime.db")
    runtime = EmbeddedNewsRuntime(Settings(path, "", "", False), "live_runtime")
    runtime.publish({"start": 0, "end": 2, "text": "Primeiro segmento confirmado."})
    runtime.publish({"start": 3, "end": 5, "text": "Segundo segmento confirmado."})
    runtime.close()

    repo = SQLiteRepository(path)
    assert len(repo.list(SpeechEvent, "live_runtime")) == 2
    assert repo.list(Discourse, "live_runtime")[0].last_event_end == 5
    repo.close()
