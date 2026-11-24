"""Export HTTP request telemetry to DataKit via OTLP/gRPC."""

import logging
import os

from fastapi import FastAPI, Request


def setup_observability(app: FastAPI) -> None:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import SpanKind, StatusCode

    resource = Resource.create({
        "service.name": os.getenv("OTEL_SERVICE_NAME", "interview-assistant"),
        "service.version": app.version,
    })
    traces = TracerProvider(resource=resource)
    traces.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    tracer = traces.get_tracer(__name__)

    logs = LoggerProvider(resource=resource)
    logs.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint)))
    request_logger = logging.getLogger("observability.http")
    request_logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=logs))

    @app.middleware("http")
    async def report_request(request: Request, call_next):
        method = request.method
        status_code = 500
        with tracer.start_as_current_span(
            method, kind=SpanKind.SERVER,
            record_exception=False, set_status_on_exception=False,
        ) as span:
            try:
                response = await call_next(request)
                status_code = response.status_code
                return response
            finally:
                # Route templates avoid exporting personal IDs or query values.
                route = getattr(request.scope.get("route"), "path", None)
                span.set_attribute("http.request.method", method)
                span.set_attribute("http.response.status_code", status_code)
                if route:
                    span.update_name(f"{method} {route}")
                    span.set_attribute("http.route", route)
                if status_code >= 500:
                    span.set_status(StatusCode.ERROR)
                request_logger.info("%s %s %d", method, route or "(unmatched)", status_code)
