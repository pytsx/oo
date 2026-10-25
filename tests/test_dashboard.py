from fastapi.testclient import TestClient

from news_machine.api import create_app
from news_machine.config import Settings
from news_machine.domain.models import Discourse, SpeechEvent
from news_machine.services.stream_supervisor import StreamSupervisor


class FakeSupervisor:
    def __init__(self): self.jobs = []
    def start(self, **values):
        job = {"id": "job_test", "stream_id": values["stream_id"] or "generated", "url": values["url"],
            "model": values["model"], "language": values["language"], "device": values["device"],
            "started_at": "2026-10-24T12:00:00Z", "status": "running", "exit_code": None, "logs": []}
        self.jobs.append(job); return job
    def list(self): return self.jobs
    def stop(self, stream_id):
        job = next((x for x in self.jobs if x["stream_id"] == stream_id and x["status"] == "running"), None)
        if not job: raise LookupError("Não existe coleta ativa")
        job["status"] = "stopped"; job["stopped_at"] = "2026-10-24T12:05:00Z"; return job
    def close(self): pass


def test_dashboard_and_operational_snapshot(tmp_path):
    supervisor = FakeSupervisor()
    app = create_app(Settings(str(tmp_path / "dashboard.db"), "", "", False, ""), supervisor)
    repo = app.state.repository
    repo.save(SpeechEvent(stream_id="live_dashboard", start=0, end=2, text="Evento confirmado"))
    repo.save(Discourse(stream_id="live_dashboard", topic="Assunto", summary="Resumo", last_event_end=2))
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "AUDIO-FIRST NEWSROOM" in page.text
        assert client.get("/operations").status_code == 200
        snapshot = client.get("/operations/snapshot").json()
        assert snapshot["metrics"]["active_streams"] == 0
        assert snapshot["streams"][0]["id"] == "live_dashboard"
        assert snapshot["discourses"][0]["topic"] == "Assunto"
        started = client.post("/streams/start", json={"url": "https://example.com/live", "stream_id": "canal-hoje"})
        assert started.status_code == 201
        assert started.json()["status"] == "running"
        assert client.get("/streams/jobs").json()[0]["stream_id"] == "canal-hoje"
        stopped = client.post("/streams/canal-hoje/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "stopped"
        workspace_page = client.get("/stream/live_dashboard")
        assert workspace_page.status_code == 200
        workspace = client.get("/streams/live_dashboard/workspace").json()
        assert workspace["metrics"]["speech_events"] == 1
        assert workspace["metrics"]["speakers"] == 1
        assert workspace["charts"]["activity"] == [{"minute": 0, "events": 1}]
        assert workspace["speakers"][0]["display_name"] == "Speaker Unknown"
        renamed = client.patch("/streams/live_dashboard/speakers/speaker_unknown",
            json={"display_name": "Apresentador", "role": "Âncora", "notes": None})
        assert renamed.status_code == 200
        assert renamed.json()["display_name"] == "Apresentador"
        repo.save(SpeechEvent(id="speech_second", stream_id="live_dashboard", start=2, end=4,
            text="Segundo evento", speaker_id="speaker_unknown"))
        merged = client.post("/speech-events/merge", json={
            "event_ids": [repo.list(SpeechEvent, "live_dashboard")[0].id, "speech_second"],
            "speaker_id": "speaker_unknown",
        })
        assert merged.status_code == 200
        assert merged.json()["start"] == 0
        health = client.get("/health").json()
        assert "merge_segments" in health["features"]


def test_stream_url_validation():
    assert StreamSupervisor.validate_url("https://youtube.com/watch?v=abc").startswith("https://")
    for invalid in ("file:///tmp/audio", "not-a-url", "ftp://example.com/live"):
        try:
            StreamSupervisor.validate_url(invalid)
        except ValueError:
            continue
        raise AssertionError(f"URL deveria ser rejeitada: {invalid}")
