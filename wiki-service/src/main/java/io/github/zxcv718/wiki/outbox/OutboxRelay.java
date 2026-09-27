package io.github.zxcv718.wiki.outbox;

import io.github.zxcv718.wiki.config.WikiProperties;
import java.time.Clock;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.connection.stream.StreamRecords;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * 아웃박스 행을 Redis Streams로 발행한다 (ADR-09).
 *
 * XADD 뒤 커밋 전에 죽으면 같은 이벤트가 다시 나간다. 인덱서가 revision으로 걸러 결과가 같으므로(ADR-19) 중복은
 * 허용하고 유실만 막는다(at-least-once).
 */
@Component
public class OutboxRelay {

    static final int BATCH_SIZE = 100;
    // 발행 기록을 며칠 남겨 두면 장애를 조사할 때 "위키가 이벤트를 냈는가"를 확인할 수 있다
    static final Duration RETENTION = Duration.ofDays(7);

    private static final Logger log = LoggerFactory.getLogger(OutboxRelay.class);

    private final OutboxRepository outbox;
    private final StringRedisTemplate redis;
    private final WikiProperties properties;
    private final Clock clock;

    public OutboxRelay(OutboxRepository outbox, StringRedisTemplate redis, WikiProperties properties, Clock clock) {
        this.outbox = outbox;
        this.redis = redis;
        this.properties = properties;
        this.clock = clock;
    }

    /**
     * 발행 안 된 행을 최대 100개 발행하고, 발행한 행 수를 돌려준다.
     *
     * 스트림 길이는 자르지 않는다(MAXLEN 없음). 인덱서가 처리를 마친 이벤트를 XACKDEL로 지우므로 스트림에는 처리 안 된
     * 이벤트만 남는다. 여기서 길이로 자르면 인덱서가 멈춘 동안 쌓인 이벤트가 처리 전에 지워질 수 있다.
     */
    @Transactional
    public int publishPending() {
        List<OutboxEvent> batch = outbox.lockUnpublished(BATCH_SIZE);
        int published = 0;
        for (OutboxEvent event : batch) {
            try {
                redis.opsForStream().add(toRecord(event));
            } catch (DataAccessException e) {
                // 실패한 행에서 멈춘다. 계속 보내면 같은 문서의 뒤 이벤트가 앞 이벤트보다 먼저 나가고, Redis가 죽은
                // 동안에는 행마다 명령 제한 시간(2초)을 기다리느라 트랜잭션을 오래 잡는다. 남은 행은 다음 주기에 보낸다.
                log.warn("아웃박스 {}번 발행에 실패해 이번 주기를 멈춥니다", event.getId(), e);
                break;
            }
            event.markPublished(clock.instant());
            published++;
        }
        return published;
    }

    @Transactional
    public int deleteExpired() {
        return outbox.deletePublishedBefore(clock.instant().minus(RETENTION));
    }

    private MapRecord<String, String, String> toRecord(OutboxEvent event) {
        Map<String, String> fields = new LinkedHashMap<>();
        String stream;
        if (event.getAggregateType() == OutboxEvent.AggregateType.DOCUMENT) {
            stream = EventStreams.documentStream(event.getAggregateId(), properties.eventPartitions());
            fields.put("doc_id", event.getAggregateId());
            fields.put("revision", String.valueOf(event.getRevision()));
        } else {
            stream = EventStreams.MEMBERSHIP;
            fields.put("user_id", event.getAggregateId());
        }
        fields.put("type", event.getEventType().name());
        fields.put("outbox_id", String.valueOf(event.getId()));
        fields.put("created_at", event.getCreatedAt().toString());
        return StreamRecords.newRecord().in(stream).ofMap(fields);
    }
}
