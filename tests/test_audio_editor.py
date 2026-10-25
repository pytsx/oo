from news_machine.domain.models import (
    Claim, ClaimKind, Relevance, SourceRef, SpeechEvent, Story, StoryStatus, TranscriptRevision,
)
from news_machine.infrastructure.repositories import SQLiteRepository
from news_machine.services.audio_editor import AudioEditor


def test_editor_preserves_revisions_and_invalidates_story(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "editor.db"))
    first = SpeechEvent(id="speech_a", stream_id="live", start=0, end=2,
        text="valor de vinte", speaker_id="speaker_01")
    second = SpeechEvent(id="speech_b", stream_id="live", start=2, end=4,
        text="milhões de reais", speaker_id="speaker_01")
    repo.save(first); repo.save(second)
    claim = Claim(id="claim_a", statement="O valor é de vinte milhões", speaker_id="speaker_01",
        kind=ClaimKind.FACTUAL, source_event_ids=[first.id, second.id], source=SourceRef(source_id="live"))
    repo.save(claim)
    story = Story(id="story_a", stream_id="live", topic="Valor anunciado", summary="Resumo",
        relevance=Relevance.HIGH, status=StoryStatus.PUBLISHABLE, claim_ids=[claim.id])
    repo.save(story)
    editor = AudioEditor(repo, str(tmp_path))

    editor.correct(first.id, "valor de trinta", "Correção manual")
    editor.assign_speaker([first.id, second.id], "speaker_02")
    merged = editor.merge([first.id, second.id])

    assert merged.text == "valor de trinta milhões de reais"
    assert repo.get(SpeechEvent, first.id).superseded_by == merged.id
    assert repo.get(Story, story.id).status == "developing"
    revisions = repo.list(TranscriptRevision, "live")
    assert [revision.operation for revision in revisions] == ["edit", "speaker_change", "merge"]


def test_word_edit_and_split_keep_word_timing(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "words.db"))
    event = SpeechEvent(id="speech_words", stream_id="live", start=10, end=14,
        text="o valor era vinte milhões", speaker_id="speaker_01")
    repo.save(event)
    editor = AudioEditor(repo, str(tmp_path))
    word = event.words[3]
    edited = editor.edit_word(event.id, word.id, "trinta")
    assert edited.text == "o valor era trinta milhões"
    split_word = edited.words[3]
    left, right = editor.split(event.id, split_word.id)
    assert left.text == "o valor era"
    assert right.text == "trinta milhões"
    assert left.end == right.start == split_word.start
    assert [x.operation for x in repo.list(TranscriptRevision, "live")] == ["word_edit", "split"]
