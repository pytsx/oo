import argparse
import asyncio
import json
from pathlib import Path

import uvicorn

from news_machine.api import create_app
from news_machine.config import Settings
from news_machine.cognition.discourse_graph import DiscoursePipeline
from news_machine.domain.models import SpeechEvent
from news_machine.infrastructure.repositories import SQLiteRepository
from news_machine.services.ollama import OllamaUnderstanding


async def ingest_file(path: str, stream_id: str, settings: Settings, batch_size: int):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    events = [SpeechEvent(stream_id=stream_id, **item) for item in raw]
    repository = SQLiteRepository(settings.db_path)
    llm = OllamaUnderstanding(settings.ollama_base_url, settings.ollama_model, settings.ollama_enabled)
    pipeline = DiscoursePipeline(repository, llm)
    for offset in range(0, len(events), batch_size):
        result = await pipeline.run(events[offset:offset + batch_size])
        print(result["discourse"].model_dump_json(indent=2))


def main():
    parser = argparse.ArgumentParser(description="Maquina autonoma de noticias")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="reiniciar automaticamente após alterações no código")
    ingest = sub.add_parser("ingest")
    ingest.add_argument("path")
    ingest.add_argument("--stream-id", default="local")
    ingest.add_argument("--batch-size", type=int, default=5)
    ingest.add_argument("--no-ollama", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    if args.command == "serve":
        if args.reload:
            uvicorn.run("news_machine.api:app", host=args.host, port=args.port, reload=True)
        else:
            uvicorn.run(create_app(settings), host=args.host, port=args.port)
    else:
        if args.no_ollama:
            settings = Settings(settings.db_path, settings.ollama_base_url, settings.ollama_model, False)
        asyncio.run(ingest_file(args.path, args.stream_id, settings, args.batch_size))


if __name__ == "__main__":
    main()

