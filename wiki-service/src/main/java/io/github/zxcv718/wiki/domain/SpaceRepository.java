package io.github.zxcv718.wiki.domain;

import jakarta.persistence.LockModeType;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface SpaceRepository extends JpaRepository<Space, String> {

    /** 같은 스페이스를 동시에 바꾸는 요청을 차례로 처리한다. 문서 잠금보다 먼저 잡는다. */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select s from Space s where s.id = :id")
    Optional<Space> findForUpdate(@Param("id") String id);

    /**
     * 스페이스에 문서를 새로 만들 때 공유 잠금을 잡는다. 스페이스 변경이 소속 문서를 고른 뒤 커밋하기 전에 새 문서가
     * 커밋되면, 새 문서는 revision이 오르지 않은 채 옛 스페이스 권한으로 색인되고 다시 바로잡히지 않는다. 공유 잠금은
     * 스페이스 변경(findForUpdate)과는 서로 기다리고, 같은 스페이스에 문서를 만드는 요청끼리는 기다리지 않는다.
     */
    @Lock(LockModeType.PESSIMISTIC_READ)
    @Query("select s from Space s where s.id = :id")
    Optional<Space> findForShare(@Param("id") String id);

    List<Space> findAllByOrderByIdAsc();
}
