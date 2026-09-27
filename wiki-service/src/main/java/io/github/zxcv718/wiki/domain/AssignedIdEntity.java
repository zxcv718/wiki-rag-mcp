package io.github.zxcv718.wiki.domain;

import jakarta.persistence.MappedSuperclass;
import jakarta.persistence.PostLoad;
import jakarta.persistence.PostPersist;
import jakarta.persistence.Transient;
import org.springframework.data.domain.Persistable;

/**
 * id를 애플리케이션이 정하는 엔티티(doc_id, 스페이스 id 등)의 공통 부모.
 *
 * Spring Data의 save()는 id가 채워진 엔티티를 기존 행으로 보고 merge한다. 그러면 같은 id가 이미 있을 때
 * 오류 없이 덮어쓰게 되므로, 새로 만든 객체는 persist(INSERT)로 가게 해 기본 키 충돌이 드러나게 한다.
 */
@MappedSuperclass
public abstract class AssignedIdEntity implements Persistable<String> {

    @Transient
    private boolean isNew = true;

    @Override
    public boolean isNew() {
        return isNew;
    }

    @PostLoad
    @PostPersist
    void markNotNew() {
        isNew = false;
    }
}
