from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from news_machine.domain.models import (
    Claim, ClaimKind, Discourse, Relevance, SourceRef, SpeechEvent, Story, Utterance,
)


class GraphState(TypedDict, total=False):
    events: list[SpeechEvent]
    previous: Discourse | None
    analysis: object
    discourse: Discourse
    claims: list[Claim]
    stories: list[Story]


class DiscoursePipeline:
    def __init__(self, repository, understanding, story_manager=None):
        self.repository, self.understanding = repository, understanding
        if story_manager is None:
            from news_machine.services.story_manager import StoryManager
            story_manager = StoryManager(repository)
        self.story_manager = story_manager
        graph = StateGraph(GraphState)
        graph.add_node("reconstruct_discourse", self.reconstruct_discourse)
        graph.add_node("extract_claims", self.extract_claims)
        graph.add_node("associate_topics", self.associate_topics)
        graph.add_node("persist_state", self.persist_state)
        graph.add_edge(START, "reconstruct_discourse")
        graph.add_edge("reconstruct_discourse", "extract_claims")
        graph.add_edge("extract_claims", "associate_topics")
        graph.add_edge("associate_topics", "persist_state")
        graph.add_edge("persist_state", END)
        self.graph = graph.compile()

    async def run(self, events: list[SpeechEvent]) -> GraphState:
        if not events:
            raise ValueError("events nao pode ser vazio")
        previous = next(iter(reversed(self.repository.list(Discourse, events[0].stream_id))), None)
        return await self.graph.ainvoke({"events": events, "previous": previous})

    async def reconstruct_discourse(self, state: GraphState):
        events, previous = state["events"], state.get("previous")
        context = previous.model_dump(mode="json") if previous else None
        analysis = await self.understanding.analyze([e.model_dump(mode="json") for e in events], context)
        identity = {"id": previous.id} if previous and previous.status == "active" else {}
        discourse = Discourse(
            **identity,
            stream_id=events[0].stream_id, topic=analysis.topic, summary=analysis.summary,
            speaker_ids=sorted(set((previous.speaker_ids if previous else []) + [e.speaker_id for e in events])),
            utterance_ids=list(previous.utterance_ids if previous else []),
            claim_ids=list(previous.claim_ids if previous else []),
            story_ids=list(previous.story_ids if previous else []), last_event_end=max(e.end for e in events),
        )
        for event in events:
            utterance = Utterance(speaker_id=event.speaker_id, text=event.text, source_event_ids=[event.id],
                source=SourceRef(source_id=event.stream_id, start=event.start, end=event.end))
            self.repository.save(utterance)
            discourse.utterance_ids.append(utterance.id)
        return {"analysis": analysis, "discourse": discourse}

    async def extract_claims(self, state: GraphState):
        events, analysis, discourse = state["events"], state["analysis"], state["discourse"]
        by_id = {e.id: e for e in events}
        claims = []
        for item in analysis.claims:
            source_ids = [x for x in item.get("source_event_ids", []) if x in by_id] or [e.id for e in events]
            source_events = [by_id[x] for x in source_ids]
            claims.append(Claim(statement=item["statement"], speaker_id=item.get("speaker_id", source_events[-1].speaker_id),
                kind=ClaimKind(item.get("kind", "factual")), source_event_ids=source_ids,
                source=SourceRef(source_id=events[0].stream_id, start=min(e.start for e in source_events), end=max(e.end for e in source_events)),
                entities=item.get("entities", [])))
        discourse.claim_ids.extend(c.id for c in claims)
        return {"claims": claims, "discourse": discourse}

    async def associate_topics(self, state: GraphState):
        candidate, discourse = state["analysis"].news_candidate, state["discourse"]
        if not candidate:
            return {"stories": []}
        story, created = self.story_manager.associate(
            stream_id=discourse.stream_id, topic=candidate["topic"],
            summary=candidate.get("summary", discourse.summary),
            relevance=Relevance(candidate.get("relevance", "medium")),
            claim_ids=[c.id for c in state["claims"]],
            requires_external_verification=candidate.get("requires_external_verification", True),
        )
        if story.id not in discourse.story_ids:
            discourse.story_ids.append(story.id)
        return {"stories": [story], "discourse": discourse}

    async def persist_state(self, state: GraphState):
        for event in state["events"]:
            self.repository.save(event)
        for claim in state["claims"]:
            self.repository.save(claim)
        for story in state["stories"]:
            self.repository.save(story)
        self.repository.save(state["discourse"])
        return {}

