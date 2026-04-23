"""SSE event emission with metrics tracking."""

from dataclasses import dataclass, field
from time import perf_counter
from typing import AsyncGenerator, Any

from api.services.streaming import SseEventBuilder


@dataclass
class StreamMetrics:
    """Tracks streaming performance metrics."""
    first_event_ms: float | None = None
    first_token_ms: float | None = None
    last_chunk_time: float | None = None
    total_chunk_interval_ms: float = 0.0
    chunk_count: int = 0


class StreamEventEmitter:
    """Handles SSE event formatting and stream metrics."""
    
    def __init__(self, start_time: float):
        """Initialize emitter with stream start time.
        
        Args:
            start_time: perf_counter() at stream start
        """
        self.builder = SseEventBuilder()
        self.start_time = start_time
        self.metrics = StreamMetrics()
    
    def emit(self, event: str, payload: dict | str) -> str:
        """Format and emit SSE event with timing.
        
        Args:
            event: Event type (start, meta, delta, done, error)
            payload: Event data dict or string
            
        Returns:
            Formatted SSE message
        """
        if self.metrics.first_event_ms is None:
            self.metrics.first_event_ms = (perf_counter() - self.start_time) * 1000
        
        if isinstance(payload, dict):
            return self.builder.emit_json(event, payload)
        return self.builder.emit(event, payload)
    
    async def emit_content_chunks(
        self,
        content: str,
        chunk_size: int = 400,
        assistant_message_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Emit content as SSE delta events.
        
        DRY pattern: consolidates 4+ copy-paste chunking blocks.
        
        Args:
            content: Full response content to chunk
            chunk_size: Bytes per chunk (default 400)
            assistant_message_id: ID to include in delta events
            
        Yields:
            SSE delta events
        """
        for index in range(0, len(content), chunk_size):
            chunk = content[index : index + chunk_size]
            self.record_chunk()
            yield self.emit("delta", {
                "delta": chunk,
                "assistant_message_id": assistant_message_id
            })
    
    def record_chunk(self) -> None:
        """Update chunk timing metrics.
        
        Tracks:
        - First token latency (ms from stream start to first chunk)
        - Inter-chunk intervals (for throughput analysis)
        - Total chunk count
        """
        now = perf_counter()
        
        if self.metrics.first_token_ms is None:
            self.metrics.first_token_ms = (now - self.start_time) * 1000
        
        if self.metrics.last_chunk_time is not None:
            interval = (now - self.metrics.last_chunk_time) * 1000
            self.metrics.total_chunk_interval_ms += interval
        
        self.metrics.last_chunk_time = now
        self.metrics.chunk_count += 1
