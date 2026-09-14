import logging
from abc import ABC, abstractmethod

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)


class ChunkingStrategy(ABC):
    @abstractmethod
    def chunk(self, text: str) -> list[str]:
        pass

    @property
    def chunk_size(self) -> int:
        return self.splitter._chunk_size

    @property
    def chunk_overlap(self) -> int:
        return self.splitter._chunk_overlap


class BlogChunking(ChunkingStrategy):
    """
    Paragraph-aware chunking for long-form blog content.
    Larger chunks preserve narrative context.
    """
    def __init__(self):
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=100,
            separators=["\n\n", "\n", ".", " "]
        )

    def chunk(self, text: str) -> list[str]:
        return self.splitter.split_text(text)


class AdChunking(ChunkingStrategy):
    """
    Sentence-level chunking for ad copy.
    Short, punchy chunks preserve the intent of each line.
    """
    def __init__(self):
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=300,
            chunk_overlap=30,
            separators=[".\n", ".", "\n", " "]
        )

    def chunk(self, text: str) -> list[str]:
        return self.splitter.split_text(text)


class ProposalChunking(ChunkingStrategy):
    """
    Section-aware chunking for long proposal documents.
    Large chunks with high overlap preserve section continuity.
    """
    def __init__(self):
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=150,
            separators=["\n\n\n", "\n\n", "\n", ".", " "]
        )

    def chunk(self, text: str) -> list[str]:
        return self.splitter.split_text(text)


class ScriptChunking(ChunkingStrategy):
    """
    Scene- and line-aware chunking for video, audio and presentation scripts.
    Splits on blank lines between scenes or beats first, then on individual
    lines, so a speaker's line is not cut away from its cue.
    """
    def __init__(self):
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=600,
            chunk_overlap=80,
            separators=["\n\n", "\n", ".", " "]
        )

    def chunk(self, text: str) -> list[str]:
        return self.splitter.split_text(text)


# Registry — maps content_type to its strategy.
# Add new content types here and in schema.CONTENT_TYPES.
CHUNKING_REGISTRY: dict[str, ChunkingStrategy] = {
    "blog":          BlogChunking(),
    "ad":            AdChunking(),
    "proposal":      ProposalChunking(),
    "script":        ScriptChunking(),
    "press_release": ProposalChunking(),     # section-aware, same structure as proposals
}

_DEFAULT_STRATEGY = BlogChunking()


def get_chunking_strategy(content_type: str) -> ChunkingStrategy:
    """
    Returns the chunking strategy for the given content type.
    Falls back to BlogChunking for unknown types with a warning.
    """
    strategy = CHUNKING_REGISTRY.get(content_type)
    if not strategy:
        logger.warning(
            "No chunking strategy for content type '%s', falling back to default (BlogChunking). "
            "Available types: %s", content_type, list(CHUNKING_REGISTRY.keys())
        )
        return _DEFAULT_STRATEGY
    return strategy
