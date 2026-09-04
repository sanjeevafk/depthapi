"""
registry.py — Plugin registry for the RAG ingestion pipeline.

The registry maps plugin type names (as used in YAML configs) to their
concrete implementation classes. All plugins are registered here.

Design:
    - Central, explicit registration (no magic auto-discovery)
    - Type-safe: separate registries per plugin type
    - Lazy instantiation: classes are stored, instances created on demand
    - Fail-fast: missing plugin name raises descriptive error at config load time

Usage:
    registry = PluginRegistry.default()
    source_cls = registry.get_source("LocalDirSource")
    source = source_cls(config)
"""

from __future__ import annotations

from api.services.rag.pipeline.interfaces import (
    BaseChunker,
    BaseMiddleware,
    BaseParser,
    BaseSink,
    BaseSource,
)


class PluginRegistry:
    """
    Central registry mapping YAML plugin names to implementation classes.

    All built-in plugins are registered in the `default()` factory.
    Custom plugins can be added via register_*() methods.
    """

    def __init__(self) -> None:
        self._sources: dict[str, type[BaseSource]] = {}
        self._parsers: dict[str, type[BaseParser]] = {}
        self._middleware: dict[str, type[BaseMiddleware]] = {}
        self._chunkers: dict[str, type[BaseChunker]] = {}
        self._sinks: dict[str, type[BaseSink]] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register_source(self, name: str, cls: type[BaseSource]) -> None:
        self._sources[name] = cls

    def register_parser(self, name: str, cls: type[BaseParser]) -> None:
        self._parsers[name] = cls

    def register_middleware(self, name: str, cls: type[BaseMiddleware]) -> None:
        self._middleware[name] = cls

    def register_chunker(self, name: str, cls: type[BaseChunker]) -> None:
        self._chunkers[name] = cls

    def register_sink(self, name: str, cls: type[BaseSink]) -> None:
        self._sinks[name] = cls

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def get_source(self, name: str) -> type[BaseSource]:
        if name not in self._sources:
            available = sorted(self._sources)
            raise KeyError(
                f"Unknown source plugin '{name}'. "
                f"Available: {available}. "
                "Register it via registry.register_source()."
            )
        return self._sources[name]

    def get_parser(self, name: str) -> type[BaseParser]:
        if name not in self._parsers:
            available = sorted(self._parsers)
            raise KeyError(
                f"Unknown parser plugin '{name}'. "
                f"Available: {available}. "
                "Register it via registry.register_parser()."
            )
        return self._parsers[name]

    def get_middleware(self, name: str) -> type[BaseMiddleware]:
        if name not in self._middleware:
            available = sorted(self._middleware)
            raise KeyError(
                f"Unknown middleware plugin '{name}'. "
                f"Available: {available}. "
                "Register it via registry.register_middleware()."
            )
        return self._middleware[name]

    def get_chunker(self, name: str) -> type[BaseChunker]:
        if name not in self._chunkers:
            available = sorted(self._chunkers)
            raise KeyError(
                f"Unknown chunker plugin '{name}'. "
                f"Available: {available}. "
                "Register it via registry.register_chunker()."
            )
        return self._chunkers[name]

    def get_sink(self, name: str) -> type[BaseSink]:
        if name not in self._sinks:
            available = sorted(self._sinks)
            raise KeyError(
                f"Unknown sink plugin '{name}'. "
                f"Available: {available}. "
                "Register it via registry.register_sink()."
            )
        return self._sinks[name]

    # ── Introspection ─────────────────────────────────────────────────────────

    def list_plugins(self) -> dict[str, list[str]]:
        """Return all registered plugin names by category."""
        return {
            "sources": sorted(self._sources),
            "parsers": sorted(self._parsers),
            "middleware": sorted(self._middleware),
            "chunkers": sorted(self._chunkers),
            "sinks": sorted(self._sinks),
        }

    # ── Default factory ───────────────────────────────────────────────────────

    @classmethod
    def default(cls) -> PluginRegistry:
        """
        Build the default registry with all built-in plugins registered.

        Imports are deferred here so missing optional dependencies
        do not fail at module load time.
        """
        registry = cls()

        # Sources
        from api.services.rag.pipeline.sources.local_dir_source import LocalDirSource
        registry.register_source("LocalDirSource", LocalDirSource)

        # Parsers
        from api.services.rag.pipeline.parsers.markdown_parser import MarkdownParser
        registry.register_parser("MarkdownParser", MarkdownParser)

        # Middleware
        from api.services.rag.pipeline.middleware.ascii_diagram_preserver import (
            AsciiDiagramPreserver,
        )
        from api.services.rag.pipeline.middleware.code_snippet_extractor import (
            CodeSnippetExtractor,
        )
        from api.services.rag.pipeline.middleware.license_appender import (
            LicenseAppender,
        )
        from api.services.rag.pipeline.middleware.toc_stripper import TocStripper
        from api.services.rag.pipeline.middleware.url_normalizer import UrlNormalizer

        registry.register_middleware("TocStripper", TocStripper)
        registry.register_middleware("AsciiDiagramPreserver", AsciiDiagramPreserver)
        registry.register_middleware("UrlNormalizer", UrlNormalizer)
        registry.register_middleware("CodeSnippetExtractor", CodeSnippetExtractor)
        registry.register_middleware("LicenseAppender", LicenseAppender)

        # Chunkers
        from api.services.rag.pipeline.chunkers.semantic_chunker import SemanticChunker
        registry.register_chunker("SemanticChunker", SemanticChunker)

        # Sinks
        from api.services.rag.pipeline.sinks.local_json_sink import LocalJsonSink

        registry.register_sink("LocalJsonSink", LocalJsonSink)

        return registry
