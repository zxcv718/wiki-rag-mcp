"""OpenTelemetry 설정 (ADR-15). 추적은 Tempo로, 지표는 Prometheus로 OTLP를 보낸다.

보낼 곳은 OpenTelemetry 표준 환경 변수로 정한다. 두 신호를 받는 곳이 달라서 신호별 주소를 쓴다.
- OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: 예) http://tempo:4318/v1/traces
- OTEL_EXPORTER_OTLP_METRICS_ENDPOINT: 예) http://prometheus:9090/api/v1/otlp/v1/metrics
주소가 없는 신호는 켜지 않는다. 그러면 계측 코드가 OpenTelemetry API의 빈 구현으로 돌아서, 테스트와 로컬 stdio
실행은 아무것도 보내지 않는다. 지표를 보내는 주기는 OTEL_METRIC_EXPORT_INTERVAL(밀리초)이다.

스팬과 지표에는 쿼리 원문과 문서 내용을 넣지 않는다. 쿼리에는 개인정보가 들어갈 수 있고(9장 "감사 로그와 호출
제한"), 권한 필터를 거치기 전의 내용은 로그 같은 곳에 남으면 안 된다(ADR-07).
"""

import os

import httpx
from opentelemetry import metrics, trace

_tracing = False


def _endpoint(signal: str) -> str | None:
    return os.environ.get(f"OTEL_EXPORTER_OTLP_{signal}_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")


def setup(service_name: str) -> None:
    """프로세스 시작 때 한 번 부른다. 서비스 이름은 Prometheus의 job 레이블과 Tempo의 서비스 이름이 된다."""
    global _tracing
    if os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        return
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({"service.name": service_name})
    if _endpoint("TRACES"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.propagate import set_global_textmap
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        # MCP 클라이언트는 요청의 _meta에 traceparent를 넣어 이 서버의 스팬을 자기 추적에 이을 수 있다(MCP 시맨틱
        # 규약). 이어 주되 "표본에서 빼라"는 표시는 따르지 않는다. 밖의 클라이언트가 자기 요청을 추적에서 빼지
        # 못하게 한다
        provider = TracerProvider(resource=resource,
                                  sampler=ParentBased(ALWAYS_ON, remote_parent_not_sampled=ALWAYS_ON))
        # baggage는 쓰지 않는다. 클라이언트가 보낸 baggage가 위키 요청 헤더로 옮겨 가지 않게 traceparent와
        # tracestate만 주고받는다
        set_global_textmap(TraceContextTextMapPropagator())
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        _tracing = True
    if _endpoint("METRICS"):
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        reader = PeriodicExportingMetricReader(OTLPMetricExporter())
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))


def instrument_http(client: httpx.Client) -> None:
    """위키 API 클라이언트의 요청에 traceparent 헤더를 붙인다. 위키가 받은 요청의 스팬이 이 추적 안에 들어온다.

    프로세스의 httpx 전체를 계측하지 않는다. 임베딩 모델을 받는 huggingface.co 요청에까지 추적 헤더가 나가기 때문이다.
    """
    if _tracing:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor.instrument_client(client)
