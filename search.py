import json
import logging
from abc import ABC, abstractmethod

from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class SearchPort(ABC):
    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> str: ...


class ParallelSearch(SearchPort):
    """Researcher-node web search backed by Parallel's Search API.

    Uses the official `parallel-web` SDK and calls `client.search(...)` at
    runtime. This is the live, in-code integration required by the Agentic
    Cinema / Parallel track.

    Docs: https://docs.parallel.ai/search/search-quickstart
    """

    # 'fast' trades a little recall for latency, which matters because this
    # call sits in front of three more LLM calls in the generation pipeline.
    DEFAULT_MODE = "fast"

    def __init__(self, api_key: str = "", mode: str = DEFAULT_MODE):
        if not api_key.strip():
            raise ValueError("Parallel API key required")
        from parallel import Parallel
        self._client = Parallel(api_key=api_key)
        self._mode = mode

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=5),
        reraise=True
    )
    def search(self, query: str, max_results: int = 5) -> str:
        # The API has no max_results parameter, so cap on our side.
        response = self._client.search(
            objective=query,
            search_queries=[query],
            mode=self._mode,
        )

        results = list(response.results or [])[:max_results]
        formatted = [
            {
                "title": getattr(r, "title", None),
                "url": getattr(r, "url", None),
                "published": str(getattr(r, "publish_date", "") or ""),
                "excerpts": list(getattr(r, "excerpts", []) or []),
            }
            for r in results
        ]

        logger.info(
            "Parallel search completed for query=%r (%d of %d results kept, mode=%s)",
            query, len(formatted), len(response.results or []), self._mode,
        )
        # json.dumps rather than str() so the researcher prompt receives valid
        # JSON instead of Python repr with single quotes.
        return json.dumps(formatted, ensure_ascii=False, indent=2)

    def search_citations(self, query: str, max_results: int = 5) -> list[dict]:
        """Same call, but returns structured results for display in the UI."""
        return json.loads(self.search(query, max_results))
