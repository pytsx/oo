import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx


@dataclass(slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    published_at: str | None = None


class SourceSearch:
    async def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        raise NotImplementedError

    async def retrieve(self, url: str) -> str:
        raise NotImplementedError


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}: self.ignored += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}: self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        if not self.ignored and data.strip(): self.parts.append(data.strip())


def _public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        return all(not ipaddress.ip_address(item[4][0]).is_private for item in addresses)
    except (socket.gaierror, ValueError):
        return False


class SearxNGSearch(SourceSearch):
    """Busca configuravel; nenhuma URL e aceita sem vir da resposta do motor."""

    def __init__(self, base_url: str, timeout: float = 20):
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    async def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        if not self.base_url:
            return []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/search", params={"q": query, "format": "json", "language": "pt-BR"})
            response.raise_for_status()
        hits = []
        for item in response.json().get("results", []):
            if _public_http_url(item.get("url", "")):
                hits.append(SearchHit(item.get("title", "Sem titulo"), item["url"], item.get("content", ""), item.get("publishedDate")))
            if len(hits) >= limit:
                break
        return hits

    async def retrieve(self, url: str) -> str:
        if not _public_http_url(url):
            raise ValueError("URL externa invalida ou privada")
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "NewsMachine/0.1"})
            response.raise_for_status()
            if not _public_http_url(str(response.url)):
                raise ValueError("redirecionamento para endereco privado")
            content = response.text[:1_000_000]
        parser = _TextExtractor()
        parser.feed(content)
        return " ".join(parser.parts)[:20_000]
