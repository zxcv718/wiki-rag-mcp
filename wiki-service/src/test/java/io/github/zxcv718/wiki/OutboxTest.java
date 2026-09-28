package io.github.zxcv718.wiki;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import io.github.zxcv718.wiki.admin.WikiAdminService;
import io.github.zxcv718.wiki.domain.DocumentRepository;
import io.github.zxcv718.wiki.outbox.EventStreams;
import io.github.zxcv718.wiki.outbox.OutboxRelay;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Range;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.transaction.support.TransactionTemplate;

class OutboxTest extends IntegrationTest {

    @Autowired
    WikiAdminService admin;

    @Autowired
    DocumentRepository documents;

    @Autowired
    OutboxRelay relay;

    @Autowired
    TransactionTemplate transaction;

    @BeforeEach
    void setUp() throws Exception {
        importFixture();
    }

    private List<MapRecord<String, Object, Object>> stream(String key) {
        return redis.opsForStream().range(key, Range.unbounded());
    }

    private long unpublished() {
        return jdbc.queryForObject("SELECT count(*) FROM outbox WHERE published_at IS NULL", Long.class);
    }

    /**
     * 문서 변경과 아웃박스 기록이 한 트랜잭션인지 확인한다. 둘 다 DB에 쓴 뒤(flush) 커밋 전에 실패시키면 둘 다 사라져야
     * 한다. 아웃박스를 별도 트랜잭션으로 쓰면 이벤트만 남고, 반대면 변경만 남는다.
     */
    @Test
    void failureBeforeCommitRollsBackChangeAndOutboxTogether() {
        assertThatThrownBy(() -> transaction.executeWithoutResult(status -> {
            admin.editContent("infra-001", "바뀐 제목", "바뀐 본문", 1);
            documents.flush();
            assertThat(jdbc.queryForObject("SELECT version FROM documents WHERE id = 'infra-001'", Integer.class))
                    .isEqualTo(2);
            assertThat(jdbc.queryForObject("SELECT count(*) FROM outbox", Long.class))
                    .isEqualTo(FIXTURE_DOCUMENTS + 1);
            throw new IllegalStateException("커밋 전 실패");
        })).isInstanceOf(IllegalStateException.class);

        assertThat(jdbc.queryForObject("SELECT version FROM documents WHERE id = 'infra-001'", Integer.class))
                .isEqualTo(1);
        assertThat(documentRevision("infra-001")).isEqualTo(1);
        assertThat(outboxRows()).hasSize(FIXTURE_DOCUMENTS);
    }

    @Test
    void relayPublishesDocumentEventsToTheirPartition() {
        assertThat(relay.publishPending()).isEqualTo(FIXTURE_DOCUMENTS);

        List<MapRecord<String, Object, Object>> partition3 = stream("wiki:events:3");
        Map<Object, Object> infra011 = partition3.stream()
                .map(MapRecord::getValue)
                .filter(fields -> "infra-011".equals(fields.get("doc_id")))
                .findFirst()
                .orElseThrow();
        // 가져오기 요청의 추적이 함께 실린다 (eventsCarryTheTraceOfTheRequestThatCausedThem)
        assertThat(infra011).containsOnlyKeys("doc_id", "revision", "type", "outbox_id", "created_at", "traceparent");
        assertThat(infra011.get("revision")).isEqualTo("5");
        assertThat(infra011.get("type")).isEqualTo("CONTENT_CHANGED");
        assertThat(infra011.get("outbox_id")).isEqualTo("4");
        assertThat(Instant.parse((String) infra011.get("created_at"))).isBefore(Instant.now().plusSeconds(1));

        int total = 0;
        for (int p = 0; p < 4; p++) {
            for (MapRecord<String, Object, Object> record : stream("wiki:events:" + p)) {
                assertThat(EventStreams.partitionOf((String) record.getValue().get("doc_id"), 4)).isEqualTo(p);
                total++;
            }
        }
        assertThat(total).isEqualTo(FIXTURE_DOCUMENTS);
        assertThat(unpublished()).isZero();
        assertThat(relay.publishPending()).isZero();
    }

    /** 같은 문서의 이벤트는 같은 스트림에 revision 순으로 들어간다. 인덱서가 한 스트림을 차례로 읽는 전제다 (ADR-19). */
    @Test
    void eventsOfOneDocumentKeepTheirOrder() throws Exception {
        relay.publishPending();
        asAdmin(put("/admin/documents/infra-011/classification")
                .contentType("application/json").content("{\"classification\": null}"))
                .andExpect(status().isOk());
        asAdmin(put("/admin/documents/infra-011/restrictions")
                .contentType("application/json").content("{\"principals\": [\"all\"]}"))
                .andExpect(status().isOk());
        relay.publishPending();

        List<String> revisions = stream("wiki:events:3").stream()
                .map(MapRecord::getValue)
                .filter(fields -> "infra-011".equals(fields.get("doc_id")))
                .map(fields -> fields.get("revision") + " " + fields.get("type"))
                .toList();
        assertThat(revisions).containsExactly("5 CONTENT_CHANGED", "6 ACL_CHANGED", "7 ACL_CHANGED");
    }

    @Test
    void relayPublishesMembershipEvents() throws Exception {
        relay.publishPending();
        asAdmin(put("/admin/groups/dba/members/seoyeon")).andExpect(status().isOk());

        assertThat(relay.publishPending()).isEqualTo(1);
        List<MapRecord<String, Object, Object>> records = stream("wiki:membership");
        assertThat(records).hasSize(1);
        assertThat(records.getFirst().getValue())
                .containsOnlyKeys("user_id", "type", "outbox_id", "created_at", "traceparent")
                .containsEntry("user_id", "seoyeon")
                .containsEntry("type", "MEMBERSHIP_CHANGED")
                .containsEntry("outbox_id", String.valueOf(FIXTURE_DOCUMENTS + 1));
    }

    /**
     * 변경을 일으킨 요청의 추적이 이벤트에 실려, 인덱서의 처리 스팬이 같은 추적에 이어진다 (ADR-15). 추적 id는 요청에
     * 들어온 traceparent의 것이고, 부모 스팬은 위키가 그 요청을 처리한 스팬이다. 요청 밖의 변경에는 싣지 않는다.
     */
    @Test
    void eventsCarryTheTraceOfTheRequestThatCausedThem() throws Exception {
        relay.publishPending();
        String traceId = "4bf92f3577b34da6a3ce929d0e0e4736";
        asAdmin(put("/admin/groups/dba/members/seoyeon").header("traceparent", "00-" + traceId + "-00f067aa0ba902b7-01"))
                .andExpect(status().isOk());
        admin.removeMember("dba", "seoyeon");
        relay.publishPending();

        List<Map<Object, Object>> events = stream("wiki:membership").stream().map(MapRecord::getValue).toList();
        assertThat(events).hasSize(2);
        assertThat((String) events.get(0).get("traceparent")).matches("00-" + traceId + "-[0-9a-f]{16}-01")
                .doesNotContain("00f067aa0ba902b7");
        assertThat(events.get(1)).doesNotContainKey("traceparent");
    }

    /**
     * 추적 맥락은 W3C traceparent로만 받는다. Caddy가 인터넷에서 온 traceparent를 지워도 B3 헤더를 받으면, 밖의 클라이언트가
     * "표본에서 빼라"는 표시로 로그인 시도 같은 요청을 추적에서 뺄 수 있다.
     */
    @Test
    void b3HeadersDoNotSteerTheTrace() throws Exception {
        relay.publishPending();
        String traceId = "4bf92f3577b34da6a3ce929d0e0e4736";
        asAdmin(put("/admin/groups/dba/members/seoyeon").header("b3", traceId + "-00f067aa0ba902b7-0"))
                .andExpect(status().isOk());
        relay.publishPending();

        // 새로 시작한 추적이고 표본에 들어 있다. 새 추적 id에는 "무작위 id" 표시(0x02)가 함께 붙어 03이 된다
        String traceParent = (String) stream("wiki:membership").getFirst().getValue().get("traceparent");
        assertThat(traceParent).matches("00-[0-9a-f]{32}-[0-9a-f]{16}-0[13]").doesNotContain(traceId);
    }

    /** 두 요청이 같은 문서를 동시에 고쳐도 행 잠금으로 차례가 정해져 revision이 겹치지 않는다. */
    @Test
    void concurrentChangesGetDistinctRevisions() throws Exception {
        int writers = 8;
        ExecutorService pool = Executors.newFixedThreadPool(writers);
        try {
            List<Callable<Object>> tasks = new ArrayList<>();
            for (int i = 0; i < writers; i++) {
                String principal = "user:writer" + i;
                tasks.add(() -> admin.changeRestrictions("infra-011", List.of(principal)));
            }
            for (Future<Object> future : pool.invokeAll(tasks)) {
                future.get();
            }
        } finally {
            pool.shutdown();
        }

        assertThat(documentRevision("infra-011")).isEqualTo(5 + writers);
        List<Long> revisions = jdbc.queryForList(
                "SELECT revision FROM outbox WHERE aggregate_id = 'infra-011' AND event_type = 'ACL_CHANGED'"
                        + " ORDER BY revision", Long.class);
        assertThat(revisions).containsExactly(6L, 7L, 8L, 9L, 10L, 11L, 12L, 13L);
    }

    @Test
    void expiredPublishedRowsAreDeleted() {
        relay.publishPending();
        jdbc.update("UPDATE outbox SET published_at = now() - interval '8 days' WHERE aggregate_id = 'co-001'");
        jdbc.update("UPDATE outbox SET published_at = NULL WHERE aggregate_id = 'eng-022'");

        assertThat(relay.deleteExpired()).isEqualTo(1);
        assertThat(outboxRows()).hasSize(FIXTURE_DOCUMENTS - 1);
        assertThat(unpublished()).isEqualTo(1);
    }
}
