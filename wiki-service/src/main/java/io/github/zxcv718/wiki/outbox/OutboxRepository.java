package io.github.zxcv718.wiki.outbox;

import java.time.Instant;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface OutboxRepository extends JpaRepository<OutboxEvent, Long> {

    /**
     * 발행 안 된 행을 id 순으로 잠가 가져온다. SKIP LOCKED라 폴러가 여러 개 떠 있어도 같은 행을 두 번 잡지 않고,
     * 다른 폴러가 잡은 행을 기다리지도 않는다. 이때 같은 문서의 이벤트가 앞뒤가 바뀌어 나갈 수 있지만, 인덱서는
     * 이벤트 순서가 아니라 revision으로 판단하므로 결과가 같다 (ADR-19).
     */
    @Query(value = """
            SELECT * FROM outbox
            WHERE published_at IS NULL
            ORDER BY id
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
            """, nativeQuery = true)
    List<OutboxEvent> lockUnpublished(@Param("limit") int limit);

    @Modifying
    @Query("delete from OutboxEvent e where e.publishedAt < :before")
    int deletePublishedBefore(@Param("before") Instant before);
}
