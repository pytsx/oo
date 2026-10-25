from news_machine.domain.models import Claim, ClaimKind, Discourse, SourceRef, SpeakerProfile, SpeechEvent
from news_machine.infrastructure.repositories import SQLiteRepository
from news_machine.services.speakers import SpeakerManager


def test_name_and_merge_speaker_clusters(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "speakers.db"))
    for speaker, index in (("speaker_01", 1), ("speaker_03", 2)):
        repo.save(SpeechEvent(id=f"speech_{index}", stream_id="live", start=index, end=index + 1,
            text="Trecho de fala", speaker_id=speaker))
        repo.save(Claim(id=f"claim_{index}", statement="Afirmação", speaker_id=speaker,
            kind=ClaimKind.FACTUAL, source_event_ids=[f"speech_{index}"], source=SourceRef(source_id="live")))
    repo.save(Discourse(stream_id="live", topic="Tema", summary="Resumo",
        speaker_ids=["speaker_01", "speaker_03"]))
    manager = SpeakerManager(repo)
    manager.update("live", "speaker_01", display_name="Ana Souza", role="Apresentadora", notes=None)
    merged = manager.merge("live", ["speaker_03"], "speaker_01")
    assert merged.display_name == "Ana Souza"
    assert set(merged.cluster_ids) == {"speaker_01", "speaker_03"}
    assert {event.speaker_id for event in repo.list(SpeechEvent, "live")} == {"speaker_01"}
    assert {claim.speaker_id for claim in repo.list(Claim, "live")} == {"speaker_01"}
    assert len(repo.list(SpeakerProfile, "live")) == 2
