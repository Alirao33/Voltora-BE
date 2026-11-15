"""Infrastructure integration: metrics."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

METRICS_REGISTRY = CollectorRegistry()


HTTP_REQUESTS = Counter(
    "voltora_http_requests_total", "HTTP requests", ["method", "path", "status"], registry=METRICS_REGISTRY
)


HTTP_LATENCY = Histogram(
    "voltora_http_request_seconds", "HTTP latency", ["method", "path"], registry=METRICS_REGISTRY
)


TELEMETRY_MSGS = Counter(
    "voltora_telemetry_messages_total", "Telemetry messages", ["result"], registry=METRICS_REGISTRY
)


ALERTS = Counter(
    "voltora_alerts_total", "Alerts raised", ["type", "severity"], registry=METRICS_REGISTRY
)


ORDERS = Counter(
    "voltora_orders_total", "Orders by final state", ["status"], registry=METRICS_REGISTRY
)
