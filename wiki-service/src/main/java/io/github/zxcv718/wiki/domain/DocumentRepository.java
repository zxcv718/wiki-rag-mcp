package io.github.zxcv718.wiki.domain;

import jakarta.persistence.LockModeType;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface DocumentRepository extends JpaRepository<Document, String> {

    /**
     * 문서를 고치기 전에 행 잠금을 잡는다. 두 요청이 같은 revision을 읽고 둘 다 +1 해서 같은 번호를 쓰는 일을 막는다.
     * 번호가 겹치면 인덱서가 뒤 이벤트를 "이미 반영됨"으로 버린다 (ADR-19).
     * Hibernate는 PostgreSQL에서 FOR NO KEY UPDATE로 잠근다. 같은 잠금끼리는 서로 기다리고, 이 행을 참조하는 외래 키
     * 검사(제한 목록 행 추가)는 막지 않는다.
     */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select d from Document d where d.id = :id")
    Optional<Document> findForUpdate(@Param("id") String id);

    /** 스페이스 변경 때 소속 문서를 잠근다. id 순으로 읽어 문서마다 쓰는 아웃박스 행의 순서가 실행마다 같게 한다. */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select d from Document d where d.space = :space and d.deleted = false order by d.id")
    List<Document> findLiveInSpaceForUpdate(@Param("space") Space space);

    /**
     * 전체 색인용 페이지. after 다음 id부터 읽는 키셋 방식이라, 오프셋 방식과 달리 읽는 도중 문서가 생기거나 삭제돼도
     * 페이지 경계가 밀려 문서를 건너뛰지 않는다.
     */
    List<Document> findByDeletedFalseAndIdGreaterThanOrderByIdAsc(String after, Limit limit);

    /**
     * 문서와 스페이스, 스페이스 보기 권한, 제한 목록을 쿼리 하나로 읽는다. 사용자별 문서 조회는 문서가 없을 때와 있지만
     * 권한이 없을 때 같은 쿼리만 실행해야, 응답 시간으로 문서의 존재가 드러나지 않는다 (ADR-08). 목록 두 개를 함께
     * 조인하면 행이 곱으로 늘지만, 권한 목록은 몇 개뿐이다.
     */
    @Query("select d from Document d join fetch d.space s left join fetch s.viewers left join fetch d.restrictions"
            + " where d.id = :id")
    Optional<Document> findWithAccess(@Param("id") String id);

    @Query("select new io.github.zxcv718.wiki.domain.DocumentRevision(d.id, d.revision, d.deleted)"
            + " from Document d order by d.id")
    List<DocumentRevision> findAllRevisions();
}
