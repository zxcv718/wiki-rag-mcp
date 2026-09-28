package io.github.zxcv718.wiki.outbox;

import io.github.zxcv718.wiki.domain.Document;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

/**
 * 아웃박스 행 (ADR-09). 문서 변경과 같은 트랜잭션에서 쓴다. 그래서 "문서는 바뀌었는데 이벤트는 없는" 상태가 생기지
 * 않는다.
 *
 * 이벤트에는 상태를 싣지 않는다. 인덱서는 이벤트를 "바뀌었다"는 신호로만 쓰고 현재 상태를 위키에서 다시 읽는다
 * (ADR-19).
 */
@Entity
@Table(name = "outbox")
public class OutboxEvent {

    public enum AggregateType { DOCUMENT, USER }

    public enum EventType { CONTENT_CHANGED, ACL_CHANGED, DELETED, MEMBERSHIP_CHANGED }

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Enumerated(EnumType.STRING)
    private AggregateType aggregateType;

    private String aggregateId;

    // 문서 이벤트는 변경 뒤의 revision을 담는다. 이벤트 형식 (doc_id, version, type)의 version 자리다 (ADR-19)
    private Long revision;

    @Enumerated(EnumType.STRING)
    private EventType eventType;

    // 인덱싱 지연(5장 "측정 지표")을 이 시각부터 잰다
    private Instant createdAt;

    // 변경을 일으킨 요청의 W3C traceparent. 추적 중이 아니었으면 null (ADR-15, TraceParent)
    private String traceParent;

    private Instant publishedAt;

    protected OutboxEvent() {
    }

    private OutboxEvent(AggregateType aggregateType, String aggregateId, Long revision, EventType eventType,
                        Instant createdAt, String traceParent) {
        this.aggregateType = aggregateType;
        this.aggregateId = aggregateId;
        this.revision = revision;
        this.eventType = eventType;
        this.createdAt = createdAt;
        this.traceParent = traceParent;
    }

    /** 문서를 바꾼 직후에 부른다. 그때의 revision을 그대로 담는다. */
    public static OutboxEvent forDocument(Document document, EventType type, Instant now, String traceParent) {
        return new OutboxEvent(AggregateType.DOCUMENT, document.getId(), document.getRevision(), type, now,
                traceParent);
    }

    public static OutboxEvent membershipChanged(String userId, Instant now, String traceParent) {
        return new OutboxEvent(AggregateType.USER, userId, null, EventType.MEMBERSHIP_CHANGED, now, traceParent);
    }

    void markPublished(Instant now) {
        this.publishedAt = now;
    }

    public Long getId() {
        return id;
    }

    public AggregateType getAggregateType() {
        return aggregateType;
    }

    public String getAggregateId() {
        return aggregateId;
    }

    public Long getRevision() {
        return revision;
    }

    public EventType getEventType() {
        return eventType;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getPublishedAt() {
        return publishedAt;
    }

    public String getTraceParent() {
        return traceParent;
    }
}
