from datetime import datetime, timezone

from news_machine.domain.models import Claim, Discourse, SpeakerProfile, SpeechEvent, Utterance


class SpeakerManager:
    def __init__(self, repository):
        self.repository = repository

    def profiles(self, stream_id: str) -> list[SpeakerProfile]:
        profiles = {item.speaker_id: item for item in self.repository.list(SpeakerProfile, stream_id)}
        events = self.repository.list(SpeechEvent, stream_id)
        for speaker_id in sorted({event.speaker_id for event in events}):
            if speaker_id not in profiles:
                profile = SpeakerProfile(stream_id=stream_id, speaker_id=speaker_id,
                    display_name=speaker_id.replace("_", " ").title(), cluster_ids=[speaker_id],
                    sample_event_ids=[x.id for x in events if x.speaker_id == speaker_id][:20])
                self.repository.save(profile)
                profiles[speaker_id] = profile
        return [item for item in profiles.values() if not item.merged_into]

    def update(self, stream_id: str, speaker_id: str, *, display_name: str, role: str | None, notes: str | None) -> SpeakerProfile:
        profile = next((x for x in self.profiles(stream_id) if x.speaker_id == speaker_id), None)
        if profile is None:
            raise LookupError("Interlocutor não encontrado")
        profile.display_name, profile.role, profile.notes = display_name.strip(), role, notes
        profile.updated_at = datetime.now(timezone.utc)
        return self.repository.save(profile)

    def merge(self, stream_id: str, source_ids: list[str], target_id: str) -> SpeakerProfile:
        profiles = {x.speaker_id: x for x in self.profiles(stream_id)}
        if target_id not in profiles or any(source not in profiles for source in source_ids):
            raise LookupError("Interlocutor não encontrado")
        target = profiles[target_id]
        sources = [x for x in source_ids if x != target_id]
        for event in self.repository.list(SpeechEvent, stream_id):
            if event.speaker_id in sources:
                event.speaker_id = target_id; self.repository.save(event)
        for utterance in self.repository.list(Utterance, stream_id):
            if utterance.speaker_id in sources:
                utterance.speaker_id = target_id; self.repository.save(utterance)
        for claim in self.repository.list(Claim, stream_id):
            if claim.speaker_id in sources:
                claim.speaker_id = target_id; self.repository.save(claim)
        for discourse in self.repository.list(Discourse, stream_id):
            discourse.speaker_ids = list(dict.fromkeys(target_id if x in sources else x for x in discourse.speaker_ids))
            self.repository.save(discourse)
        for source in sources:
            profile = profiles[source]
            target.cluster_ids = list(dict.fromkeys(target.cluster_ids + profile.cluster_ids))
            target.sample_event_ids = list(dict.fromkeys(target.sample_event_ids + profile.sample_event_ids))[:100]
            profile.merged_into = target_id; profile.updated_at = datetime.now(timezone.utc)
            self.repository.save(profile)
        target.updated_at = datetime.now(timezone.utc)
        return self.repository.save(target)
