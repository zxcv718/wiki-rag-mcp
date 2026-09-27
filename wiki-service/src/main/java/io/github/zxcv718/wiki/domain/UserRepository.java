package io.github.zxcv718.wiki.domain;

import jakarta.persistence.LockModeType;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface UserRepository extends JpaRepository<User, String> {

    /** 사용자와 소속 그룹을 쿼리 하나로 읽는다. 문서 조회의 쿼리 수를 경우마다 같게 하려는 것이다(findWithAccess). */
    @Query("select u from User u left join fetch u.groupIds where u.id = :id")
    Optional<User> findWithGroups(@Param("id") String id);

    /** 멤버십을 동시에 바꿔도 "바뀌었는가" 판단과 이벤트 기록이 어긋나지 않게 사용자 행을 잠근다. */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select u from User u where u.id = :id")
    Optional<User> findForUpdate(@Param("id") String id);
}
