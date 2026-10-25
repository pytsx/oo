from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from news_machine.config import Settings
from news_machine.cognition.discourse_graph import DiscoursePipeline
from news_machine.cognition.investigation_graph import InvestigationPipeline
from news_machine.cognition.publication_graph import PublicationPipeline
from news_machine.domain.models import Article, ArticleAudit, Claim, Discourse, Evidence, EvidenceVerdict, SpeechEvent, Story, StoryAssessment, TranscriptRevision
from news_machine.infrastructure.repositories import SQLiteRepository
from news_machine.services.ollama import OllamaUnderstanding
from news_machine.services.search import SearxNGSearch
from news_machine.services.verification import OllamaEvidenceVerifier
from news_machine.services.writing import OllamaArticleWriter
from news_machine.services.stream_supervisor import StreamSupervisor
from news_machine.services.clarification import FactClarifier
from news_machine.services.speakers import SpeakerManager
from news_machine.services.audio_editor import AudioEditor


class StartStreamRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    stream_id: str | None = Field(default=None, max_length=80)
    model: str = Field(default="small", pattern=r"^[a-zA-Z0-9_.-]+$")
    language: str = Field(default="pt", pattern=r"^[a-zA-Z-]+$")
    device: str = Field(default="cpu", pattern=r"^(cpu|cuda)$")
    use_ollama: bool = True


class UpdateSpeakerRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    role: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=1000)


class MergeSpeakersRequest(BaseModel):
    source_ids: list[str] = Field(min_length=1)
    target_id: str


class EditTranscriptRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    reason: str | None = Field(default=None, max_length=500)


class MergeSegmentsRequest(BaseModel):
    event_ids: list[str] = Field(min_length=2)
    speaker_id: str | None = None


class ReprocessSegmentRequest(BaseModel):
    model: str = Field(default="small", pattern=r"^[a-zA-Z0-9_.-]+$")
    language: str = Field(default="pt", pattern=r"^[a-zA-Z-]+$")


class AssignSpeakerRequest(BaseModel):
    event_ids: list[str] = Field(min_length=1)
    speaker_id: str


class EditWordRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200)


class SplitSegmentRequest(BaseModel):
    word_id: str


def create_app(settings: Settings | None = None, stream_supervisor=None) -> FastAPI:
    settings = settings or Settings()
    repository = SQLiteRepository(settings.db_path)
    pipeline = DiscoursePipeline(repository, OllamaUnderstanding(settings.ollama_base_url, settings.ollama_model, settings.ollama_enabled))
    verifier = OllamaEvidenceVerifier(settings.ollama_base_url, settings.ollama_model, settings.ollama_enabled)
    investigation = InvestigationPipeline(repository, SearxNGSearch(settings.searxng_base_url), verifier)
    publication = PublicationPipeline(repository, OllamaArticleWriter(settings.ollama_base_url, settings.ollama_model, settings.ollama_enabled))
    clarifier = FactClarifier(repository)
    speaker_manager = SpeakerManager(repository)
    audio_editor = AudioEditor(repository, settings.media_dir)
    supervisor = stream_supervisor or StreamSupervisor(settings)
    @asynccontextmanager
    async def lifespan(_app):
        yield
        supervisor.close()
        repository.close()

    app = FastAPI(title="News Machine", version="0.1.0", lifespan=lifespan)
    app.state.repository, app.state.pipeline = repository, pipeline
    app.state.investigation, app.state.publication = investigation, publication
    app.state.stream_supervisor = supervisor
    dashboard_dir = Path(__file__).with_name("dashboard")
    app.mount("/assets", StaticFiles(directory=dashboard_dir), name="dashboard-assets")

    @app.get("/health")
    def health():
        return {"status": "ok", "api_version": "0.4.0", "features": [
            "audio_recording", "multitrack_editor", "merge_segments",
            "assign_speaker", "transcript_revision", "segment_reprocess",
            "word_timestamps", "word_edit", "split_segment",
        ]}

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(dashboard_dir / "home.html")

    @app.get("/operations", include_in_schema=False)
    def dashboard():
        return FileResponse(dashboard_dir / "index.html")

    @app.get("/stream/{stream_id}", include_in_schema=False)
    def stream_workspace_page(stream_id: str):
        return FileResponse(dashboard_dir / "stream.html")

    @app.get("/operations/snapshot")
    def operational_snapshot():
        speech = repository.list(SpeechEvent)
        discourses = repository.list(Discourse)
        claims = repository.list(Claim)
        stories = repository.list(Story)
        evidence = repository.list(Evidence)
        verdicts = repository.list(EvidenceVerdict)
        assessments = repository.list(StoryAssessment)
        articles = repository.list(Article)
        audits = repository.list(ArticleAudit)
        jobs = supervisor.list()
        latest_job = {}
        for job in reversed(jobs):
            latest_job.setdefault(job["stream_id"], job)
        streams = sorted({x.stream_id for x in speech + discourses + stories})
        stream_rows = []
        for stream_id in streams:
            stream_speech = [x for x in speech if x.stream_id == stream_id]
            stream_discourses = [x for x in discourses if x.stream_id == stream_id]
            stream_stories = [x for x in stories if x.stream_id == stream_id]
            latest = max((x.created_at for x in stream_speech), default=None)
            has_recent_audio = bool(latest and (datetime.now(timezone.utc) - latest).total_seconds() <= 120)
            job = latest_job.get(stream_id)
            if job and job["status"] == "running":
                collection_status = "live" if has_recent_audio else ("starting" if not stream_speech else "no_signal")
            elif job:
                collection_status = job["status"]
            else:
                collection_status = "external" if has_recent_audio else "archived"
            active = collection_status == "live"
            stream_rows.append({
                "id": stream_id, "active": active, "last_event_at": latest,
                "speech_events": len(stream_speech), "discourses": len(stream_discourses),
                "stories": len(stream_stories), "collection_status": collection_status,
                "job": job,
            })
        assessment_by_story = {x.story_id: x for x in assessments}
        article_rows = []
        audit_by_article = {x.article_id: x for x in audits}
        for article in articles:
            audit = audit_by_article.get(article.id)
            article_rows.append({**article.model_dump(mode="json"),
                "audit": audit.model_dump(mode="json") if audit else None})
        return {
            "generated_at": datetime.now(timezone.utc),
            "metrics": {
                "active_streams": sum(x["active"] for x in stream_rows),
                "active_discourses": sum(x.status == "active" for x in discourses),
                "investigating_stories": sum(x.status in {"investigating", "waiting", "consolidating"} for x in stories),
                "conflicts": sum(x.stance == "contradicts" for x in verdicts),
                "review_articles": sum(x.status == "review" for x in articles),
                "publishable_articles": sum(x.status == "publishable" for x in articles),
            },
            "streams": stream_rows,
            "discourses": [x.model_dump(mode="json") for x in reversed(discourses[-30:])],
            "stories": [{**x.model_dump(mode="json"),
                "assessment": assessment_by_story[x.id].model_dump(mode="json") if x.id in assessment_by_story else None,
                "evidence_count": sum(item.story_id == x.id for item in evidence),
                "conflict_count": sum(item.story_id == x.id and item.stance == "contradicts" for item in verdicts),
            } for x in reversed(stories)],
            "evidence": [x.model_dump(mode="json") for x in reversed(evidence[-50:])],
            "verdicts": [x.model_dump(mode="json") for x in reversed(verdicts[-50:])],
            "articles": list(reversed(article_rows)),
            "status_counts": dict(Counter(x.status.value for x in stories)),
            "collection_jobs": jobs,
        }

    @app.post("/streams/start", status_code=201)
    def start_stream(request: StartStreamRequest):
        try:
            return supervisor.start(**request.model_dump())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/streams/jobs")
    def stream_jobs():
        return supervisor.list()

    @app.post("/streams/{stream_id}/stop")
    def stop_stream(stream_id: str):
        try:
            return supervisor.stop(stream_id)
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/streams/{stream_id}/workspace")
    def stream_workspace(stream_id: str):
        all_speech = repository.list(SpeechEvent, stream_id)
        speech = [event for event in all_speech if not event.superseded_by]
        discourses = repository.list(Discourse, stream_id)
        claims = repository.list(Claim, stream_id)
        stories = repository.list(Story, stream_id)
        all_evidence = repository.list(Evidence, stream_id)
        story_ids = {story.id for story in stories}
        verdicts = [item for item in repository.list(EvidenceVerdict, stream_id) if item.story_id in story_ids]
        assessments = [item for item in repository.list(StoryAssessment, stream_id) if item.story_id in story_ids]
        articles = [item for item in repository.list(Article, stream_id) if item.story_id in story_ids]
        claim_by_speaker = Counter(claim.speaker_id for claim in claims)
        kind_by_speaker: dict[str, Counter] = {}
        entity_by_speaker: dict[str, Counter] = {}
        for claim in claims:
            kind_by_speaker.setdefault(claim.speaker_id, Counter())[claim.kind.value] += 1
            entity_by_speaker.setdefault(claim.speaker_id, Counter()).update(claim.entities)
        speaker_events: dict[str, list[SpeechEvent]] = {}
        for event in speech:
            speaker_events.setdefault(event.speaker_id, []).append(event)
        speakers = []
        for speaker_id, events in speaker_events.items():
            speakers.append({
                "id": speaker_id, "turns": len(events),
                "speaking_seconds": round(sum(event.end - event.start for event in events), 1),
                "words": sum(len(event.text.split()) for event in events),
                "claim_count": claim_by_speaker[speaker_id],
                "claim_kinds": dict(kind_by_speaker.get(speaker_id, {})),
                "top_entities": entity_by_speaker.get(speaker_id, Counter()).most_common(6),
                "recent_quotes": [event.text for event in events[-3:]],
            })
        profile_by_speaker = {x.speaker_id: x for x in speaker_manager.profiles(stream_id)}
        for speaker in speakers:
            profile = profile_by_speaker.get(speaker["id"])
            speaker["display_name"] = profile.display_name if profile else speaker["id"]
            speaker["role"] = profile.role if profile else None
            speaker["notes"] = profile.notes if profile else None
            speaker["cluster_ids"] = profile.cluster_ids if profile else [speaker["id"]]
            speaker["sample_count"] = len(profile.sample_event_ids) if profile else 0
        reasoning_chains = []
        for speaker in speakers:
            speaker_claims = sorted((claim for claim in claims if claim.speaker_id == speaker["id"]), key=lambda x: x.created_at)
            nodes = []
            for index, claim in enumerate(speaker_claims):
                transition = "início"
                if index:
                    previous = speaker_claims[index - 1]
                    shared = set(previous.entities) & set(claim.entities)
                    transition = "desenvolve a mesma referência" if shared else (
                        "formula uma pergunta" if claim.kind == "question" else "acrescenta uma nova afirmação")
                nodes.append({"claim_id": claim.id, "statement": claim.statement,
                    "kind": claim.kind, "transition": transition, "source_event_ids": claim.source_event_ids})
            reasoning_chains.append({"speaker_id": speaker["id"], "display_name": speaker["display_name"], "nodes": nodes})
        entity_counts = Counter(entity for claim in claims for entity in claim.entities)
        org_terms = {"empresa", "universidade", "governo", "ministério", "banco", "instituto", "openai", "google", "meta"}
        people, entities = [], []
        for name, count in entity_counts.most_common(30):
            words = name.split()
            looks_person = 2 <= len(words) <= 4 and all(word[:1].isupper() for word in words)
            target = people if looks_person and not any(term in name.lower() for term in org_terms) else entities
            target.append({"name": name, "mentions": count, "inferred": True})
        stopwords = {"para", "como", "mais", "sobre", "essa", "esse", "isso", "uma", "com", "que", "dos", "das", "por", "não", "nao", "será", "sera", "empresa", "afirma", "disse", "anunciou"}
        entity_words = {part.lower() for name in entity_counts for part in name.split()}
        concept_counts = Counter(word.lower() for claim in claims
            for word in re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ-]{3,}", claim.statement)
            if word.lower() not in stopwords and word.lower() not in entity_words)
        activity = Counter(int(event.start // 60) for event in speech)
        latest = max((event.created_at for event in speech), default=None)
        has_recent_audio = bool(latest and (datetime.now(timezone.utc) - latest).total_seconds() <= 120)
        jobs = [job for job in supervisor.list() if job["stream_id"] == stream_id]
        job = jobs[0] if jobs else None
        if job and job["status"] == "running":
            collection_status = "live" if has_recent_audio else ("starting" if not speech else "no_signal")
        elif job:
            collection_status = job["status"]
        else:
            collection_status = "external" if has_recent_audio else "archived"
        active = collection_status == "live"
        return {
            "stream": {"id": stream_id, "active": active, "last_event_at": latest,
                "duration_seconds": max((event.end for event in speech), default=0),
                "collection_status": collection_status, "job": job},
            "capabilities": {"semantic_analysis": settings.ollama_enabled,
                "external_search": bool(settings.searxng_base_url)},
            "metrics": {"speech_events": len(speech), "discourses": len(discourses),
                "speakers": len(speakers), "claims": len(claims), "stories": len(stories),
                "evidence": len(all_evidence), "conflicts": sum(x.stance == "contradicts" for x in verdicts)},
            "charts": {"activity": [{"minute": minute, "events": activity[minute]} for minute in sorted(activity)],
                "claim_kinds": dict(Counter(claim.kind.value for claim in claims)),
                "speaking_share": [{"speaker": x["id"], "seconds": x["speaking_seconds"]} for x in speakers],
                "story_status": dict(Counter(story.status.value for story in stories))},
            "speakers": sorted(speakers, key=lambda x: x["speaking_seconds"], reverse=True),
            "reasoning_chains": reasoning_chains,
            "people": people, "entities": entities,
            "concepts": [{"name": name, "mentions": count} for name, count in concept_counts.most_common(20)],
            "discourses": [x.model_dump(mode="json") for x in reversed(discourses)],
            "speech": [x.model_dump(mode="json") for x in speech[-250:]],
            "claims": [x.model_dump(mode="json") for x in reversed(claims[-100:])],
            "stories": [x.model_dump(mode="json") for x in reversed(stories)],
            "references": [{"id": x.id, "title": x.title, "url": x.source.url,
                "publisher": x.publisher, "stance": x.stance, "quality": x.quality,
                "story_id": x.story_id} for x in reversed(all_evidence)],
            "assessments": [x.model_dump(mode="json") for x in assessments],
            "articles": [x.model_dump(mode="json") for x in reversed(articles)],
            "audio_url": f"/streams/{stream_id}/audio" if (Path(settings.media_dir).resolve() / f"{stream_id}.wav").exists() else None,
            "revisions": [x.model_dump(mode="json") for x in repository.list(TranscriptRevision, stream_id)[-50:]],
        }

    @app.post("/streams/{stream_id}/clarify")
    async def clarify_stream(stream_id: str):
        stories = clarifier.clarify(stream_id)
        investigated = []
        for story in stories:
            result = await investigation.run(story.id)
            investigated.append(result["story"])
        return {"created_or_updated": len(stories),
            "stories": [story.model_dump(mode="json") for story in investigated]}

    @app.get("/streams/{stream_id}/audio")
    def stream_audio(stream_id: str):
        media_root = Path(settings.media_dir).resolve()
        audio_path = (media_root / f"{stream_id}.wav").resolve()
        if audio_path.parent != media_root or not audio_path.exists():
            raise HTTPException(404, "Áudio ainda não disponível")
        return FileResponse(audio_path, media_type="audio/wav", filename=f"{stream_id}.wav")

    @app.patch("/streams/{stream_id}/speakers/{speaker_id}")
    def update_speaker(stream_id: str, speaker_id: str, request: UpdateSpeakerRequest):
        try:
            return speaker_manager.update(stream_id, speaker_id, **request.model_dump())
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/streams/{stream_id}/speakers/merge")
    def merge_speakers(stream_id: str, request: MergeSpeakersRequest):
        try:
            return speaker_manager.merge(stream_id, request.source_ids, request.target_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.patch("/speech-events/{event_id}")
    def edit_speech_event(event_id: str, request: EditTranscriptRequest):
        try:
            return audio_editor.correct(event_id, request.text, request.reason)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/speech-events/merge")
    def merge_speech_events(request: MergeSegmentsRequest):
        try:
            return audio_editor.merge(request.event_ids, request.speaker_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/speech-events/{event_id}/reprocess")
    def reprocess_speech_event(event_id: str, request: ReprocessSegmentRequest):
        try:
            return audio_editor.reprocess(event_id, request.model, request.language)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/speech-events/assign-speaker")
    def assign_speech_event_speaker(request: AssignSpeakerRequest):
        try:
            return audio_editor.assign_speaker(request.event_ids, request.speaker_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.patch("/speech-events/{event_id}/words/{word_id}")
    def edit_speech_word(event_id: str, word_id: str, request: EditWordRequest):
        try:
            return audio_editor.edit_word(event_id, word_id, request.text)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/speech-events/{event_id}/split")
    def split_speech_event(event_id: str, request: SplitSegmentRequest):
        try:
            left, right = audio_editor.split(event_id, request.word_id)
            return {"left": left, "right": right}
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/streams/{stream_id}/speech-events")
    async def ingest(stream_id: str, events: list[SpeechEvent]):
        if any(event.stream_id != stream_id for event in events):
            raise HTTPException(400, "stream_id da rota e dos eventos devem coincidir")
        result = await pipeline.run(events)
        return {"discourse": result["discourse"], "claims": result["claims"], "stories": result["stories"]}

    @app.get("/streams/{stream_id}/discourses")
    def discourses(stream_id: str): return repository.list(Discourse, stream_id)

    @app.get("/streams/{stream_id}/claims")
    def claims(stream_id: str): return repository.list(Claim, stream_id)

    @app.get("/streams/{stream_id}/stories")
    def stories(stream_id: str): return repository.list(Story, stream_id)

    @app.post("/stories/{story_id}/investigate")
    async def investigate(story_id: str):
        try:
            result = await investigation.run(story_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"story": result["story"], "evidence": result["evidence"], "assessment": result["assessment"]}

    @app.get("/stories/{story_id}/evidence")
    def evidence(story_id: str):
        return [item for item in repository.list(Evidence) if item.story_id == story_id]

    @app.get("/stories/{story_id}/verdicts")
    def verdicts(story_id: str):
        return [item for item in repository.list(EvidenceVerdict) if item.story_id == story_id]

    @app.get("/stories/{story_id}/assessments")
    def assessments(story_id: str):
        return [item for item in repository.list(StoryAssessment) if item.story_id == story_id]

    @app.post("/stories/{story_id}/draft")
    async def draft(story_id: str):
        try:
            result = await publication.run(story_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"story": result["story"], "article": result["article"], "audit": result["audit"]}

    @app.get("/stories/{story_id}/articles")
    def articles(story_id: str):
        return [item for item in repository.list(Article) if item.story_id == story_id]

    return app


app = create_app()

