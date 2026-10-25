import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any


class LocalEventBus:
    """Fila local com backpressure; substituivel por Redis Streams."""

    def __init__(self, maxsize: int = 256):
        self._queues: dict[str, asyncio.Queue] = defaultdict(lambda: asyncio.Queue(maxsize=maxsize))

    async def publish(self, topic: str, event: Any) -> None:
        await self._queues[topic].put(event)

    async def get(self, topic: str) -> Any:
        return await self._queues[topic].get()

    async def subscribe(self, topic: str) -> AsyncIterator[Any]:
        queue = self._queues[topic]
        while True:
            yield await queue.get()

