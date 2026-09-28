package io.github.zxcv718.wiki.outbox;

import io.micrometer.tracing.TraceContext;
import io.micrometer.tracing.Tracer;
import io.micrometer.tracing.propagation.Propagator;
import java.util.HashMap;
import java.util.Map;
import org.springframework.stereotype.Component;

/**
 * 지금 처리 중인 요청의 추적 맥락을 W3C traceparent 문자열로 만든다 (ADR-15).
 *
 * 아웃박스 행에 적어 이벤트와 함께 보내면, 인덱서가 그 이벤트를 처리하는 스팬이 문서를 고친 요청의 추적에 이어진다.
 * 폴러는 나중에 다른 스레드에서 발행하므로, 추적 맥락은 변경과 같은 트랜잭션에서 행에 적어 둬야 한다.
 */
@Component
public class TraceParent {

    private final Tracer tracer;
    private final Propagator propagator;

    public TraceParent(Tracer tracer, Propagator propagator) {
        this.tracer = tracer;
        this.propagator = propagator;
    }

    /** 추적 중이 아니면 null이다. 이벤트는 traceparent 없이도 똑같이 처리된다. */
    public String current() {
        TraceContext context = tracer.currentTraceContext().context();
        if (context == null) {
            return null;
        }
        Map<String, String> carrier = new HashMap<>();
        propagator.inject(context, carrier, Map::put);
        return carrier.get("traceparent");
    }
}
