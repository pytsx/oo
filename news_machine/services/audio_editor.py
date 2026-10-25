import wave
from pathlib import Path

import numpy as np

from news_machine.domain.models import (
    Claim, SpeechEvent, Story, StoryStatus, TranscriptRevision, Utterance, VerificationStatus, WordToken,
)


class AudioEditor:
    def __init__(self, repository, media_dir: str):
        self.repository = repository
        self.media_dir = Path(media_dir).resolve()
        self._models = {}

    def correct(self, event_id: str, text: str, reason: str | None = None,
                operation: str = "edit") -> SpeechEvent:
        event = self.repository.get(SpeechEvent, event_id)
        if event is None or event.superseded_by:
            raise LookupError("Segmento ativo não encontrado")
        before = event.text
        event.text = text.strip()
        parts = event.text.split()
        duration = event.end - event.start
        event.words = [WordToken(id=f"{event.id}_w{index:03d}", text=word,
            start=event.start + duration * index / len(parts), end=event.start + duration * (index + 1) / len(parts),
            timing_source="manual") for index, word in enumerate(parts)]
        self.repository.save(event)
        for utterance in self.repository.list(Utterance, event.stream_id):
            if event.id in utterance.source_event_ids:
                utterance.text = event.text; self.repository.save(utterance)
        self._invalidate_claims(event.stream_id, {event.id})
        self.repository.save(TranscriptRevision(stream_id=event.stream_id, event_ids=[event.id],
            operation=operation, before_text=before, after_text=event.text, reason=reason))
        return event

    def merge(self, event_ids: list[str], speaker_id: str | None = None) -> SpeechEvent:
        events = [self.repository.get(SpeechEvent, event_id) for event_id in event_ids]
        if len(events) < 2 or any(event is None or event.superseded_by for event in events):
            raise ValueError("Selecione ao menos dois segmentos ativos")
        events = sorted(events, key=lambda item: item.start)
        if len({event.stream_id for event in events}) != 1:
            raise ValueError("Segmentos devem pertencer à mesma transmissão")
        merged = SpeechEvent(stream_id=events[0].stream_id, start=events[0].start,
            end=max(event.end for event in events), text=" ".join(event.text for event in events),
            speaker_id=speaker_id or events[0].speaker_id,
            words=[word.model_copy(deep=True) for event in events for word in event.words])
        self.repository.save(merged)
        for event in events:
            event.superseded_by = merged.id; self.repository.save(event)
        self._invalidate_claims(merged.stream_id, {event.id for event in events})
        self.repository.save(TranscriptRevision(stream_id=merged.stream_id,
            event_ids=[event.id for event in events] + [merged.id], operation="merge",
            before_text=" | ".join(event.text for event in events), after_text=merged.text,
            reason="Segmentos unificados no editor"))
        return merged

    def edit_word(self, event_id: str, word_id: str, text: str) -> SpeechEvent:
        event = self.repository.get(SpeechEvent, event_id)
        if event is None or event.superseded_by:
            raise LookupError("Segmento ativo não encontrado")
        word = next((item for item in event.words if item.id == word_id), None)
        if word is None:
            raise LookupError("Palavra não encontrada")
        before = word.text
        word.text = text.strip()
        word.timing_source = "manual"
        event.text = " ".join(item.text for item in event.words)
        self.repository.save(event)
        self._invalidate_claims(event.stream_id, {event.id})
        self.repository.save(TranscriptRevision(stream_id=event.stream_id, event_ids=[event.id],
            operation="word_edit", before_text=before, after_text=word.text,
            reason=f"Correção da palavra {word_id}"))
        return event

    def split(self, event_id: str, word_id: str) -> tuple[SpeechEvent, SpeechEvent]:
        event = self.repository.get(SpeechEvent, event_id)
        if event is None or event.superseded_by:
            raise LookupError("Segmento ativo não encontrado")
        index = next((i for i, word in enumerate(event.words) if word.id == word_id), -1)
        if index <= 0 or index >= len(event.words):
            raise ValueError("Escolha uma palavra interna como ponto de corte")
        left_words, right_words = event.words[:index], event.words[index:]
        boundary = right_words[0].start
        left = SpeechEvent(stream_id=event.stream_id, start=event.start, end=boundary,
            text=" ".join(word.text for word in left_words), speaker_id=event.speaker_id,
            words=[word.model_copy(deep=True) for word in left_words])
        right = SpeechEvent(stream_id=event.stream_id, start=boundary, end=event.end,
            text=" ".join(word.text for word in right_words), speaker_id=event.speaker_id,
            words=[word.model_copy(deep=True) for word in right_words])
        self.repository.save(left); self.repository.save(right)
        event.superseded_by = f"split:{left.id},{right.id}"
        self.repository.save(event)
        self._invalidate_claims(event.stream_id, {event.id})
        self.repository.save(TranscriptRevision(stream_id=event.stream_id,
            event_ids=[event.id, left.id, right.id], operation="split", before_text=event.text,
            after_text=f"{left.text} | {right.text}", reason=f"Corte antes de {right_words[0].text}"))
        return left, right

    def reprocess(self, event_id: str, model_name: str = "small", language: str = "pt") -> SpeechEvent:
        event = self.repository.get(SpeechEvent, event_id)
        if event is None or event.superseded_by:
            raise LookupError("Segmento ativo não encontrado")
        audio_path = (self.media_dir / f"{event.stream_id}.wav").resolve()
        if audio_path.parent != self.media_dir or not audio_path.exists():
            raise FileNotFoundError("Gravação de áudio não encontrada")
        with wave.open(str(audio_path), "rb") as source:
            rate = source.getframerate()
            start = max(0, int((event.start - .5) * rate))
            end = min(source.getnframes(), int((event.end + .5) * rate))
            source.setpos(start)
            raw = source.readframes(end - start)
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if model_name not in self._models:
            from faster_whisper import WhisperModel
            self._models[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, _ = self._models[model_name].transcribe(samples,
            language=None if language == "auto" else language, vad_filter=True, beam_size=5,
            word_timestamps=True)
        segments = list(segments)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if not text:
            raise ValueError("O reprocessamento não reconheceu fala")
        updated = self.correct(event_id, text, reason=f"Reprocessado com {model_name}", operation="reprocess")
        offset = start / rate
        timed_words = [word for segment in segments for word in (segment.words or []) if word.word.strip()]
        if timed_words:
            updated.words = [WordToken(id=f"{updated.id}_w{index:03d}", text=word.word.strip(),
                start=offset + word.start, end=offset + word.end,
                confidence=getattr(word, "probability", None), timing_source="whisper")
                for index, word in enumerate(timed_words)]
            updated.text = " ".join(word.text for word in updated.words)
            self.repository.save(updated)
        return updated

    def assign_speaker(self, event_ids: list[str], speaker_id: str) -> list[SpeechEvent]:
        events = [self.repository.get(SpeechEvent, event_id) for event_id in event_ids]
        if not events or any(event is None or event.superseded_by for event in events):
            raise ValueError("Seleção contém segmentos inválidos")
        if len({event.stream_id for event in events}) != 1:
            raise ValueError("Segmentos devem pertencer à mesma transmissão")
        stream_id = events[0].stream_id
        before = ", ".join(f"{event.id}:{event.speaker_id}" for event in events)
        for event in events:
            event.speaker_id = speaker_id
            self.repository.save(event)
        for utterance in self.repository.list(Utterance, stream_id):
            if set(utterance.source_event_ids) & set(event_ids):
                utterance.speaker_id = speaker_id; self.repository.save(utterance)
        for claim in self.repository.list(Claim, stream_id):
            if set(claim.source_event_ids) & set(event_ids):
                claim.speaker_id = speaker_id; self.repository.save(claim)
        self._invalidate_claims(stream_id, set(event_ids))
        self.repository.save(TranscriptRevision(stream_id=stream_id, event_ids=event_ids,
            operation="speaker_change", before_text=before,
            after_text=f"{len(events)} segmento(s) → {speaker_id}", reason="Reatribuição por drag and drop"))
        return events

    def _invalidate_claims(self, stream_id: str, event_ids: set[str]):
        affected_claims = set()
        for claim in self.repository.list(Claim, stream_id):
            if event_ids & set(claim.source_event_ids):
                claim.verification_status = VerificationStatus.PENDING
                self.repository.save(claim)
                affected_claims.add(claim.id)
        for story in self.repository.list(Story, stream_id):
            if affected_claims & set(story.claim_ids):
                story.status = StoryStatus.DEVELOPING
                self.repository.save(story)
