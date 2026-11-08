"""Tracing setup. By default traces carry tokens, latency and cost, never content.

With COACH_LOG_CONTENT=true, prompts and outputs are recorded for debugging, and nothing is
sent to Logfire: they stay on this machine, in a git-ignored JSONL file.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import logfire
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

from coach.config import Settings


class JsonlSpanExporter(SpanExporter):
    """Appends each finished span to a local file, one JSON object per line."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as file:
                for span in spans:
                    file.write(json.dumps(json.loads(span.to_json()), ensure_ascii=False) + "\n")
        except OSError:
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS


def send_to_logfire_mode(settings: Settings) -> bool | Literal["if-token-present"]:
    return False if settings.log_content else "if-token-present"


def configure_observability(
    settings: Settings,
    service_name: str,
    *,
    write_content_log: bool = False,
    span_processors: Sequence[SpanProcessor] = (),
) -> None:
    """Call once at startup. The console stays off: stdout is the MCP server's protocol."""
    token = settings.logfire_token.get_secret_value() if settings.logfire_token else None
    processors = list(span_processors)
    if settings.log_content and write_content_log:
        processors.append(SimpleSpanProcessor(JsonlSpanExporter(settings.content_log_path)))
    logfire.configure(
        service_name=service_name,
        send_to_logfire=send_to_logfire_mode(settings),
        token=token,
        console=False,
        inspect_arguments=False,
        # The MCP server continues the runner's trace on purpose: tools/call carries its context.
        distributed_tracing=True,
        additional_span_processors=processors,
    )
    logfire.instrument_pydantic_ai(
        include_content=settings.log_content, include_binary_content=False
    )
