package io.github.zxcv718.wiki.domain;

import jakarta.persistence.CollectionTable;
import jakarta.persistence.Column;
import jakarta.persistence.ElementCollection;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.Table;
import java.util.Collection;
import java.util.HashSet;
import java.util.Set;

/**
 * 스페이스. 보기 권한(viewers)과 기본 등급을 문서에 물려준다.
 *
 * 스페이스를 바꾸면 소속 문서의 revision도 올려야 하는데(ADR-19), 여러 문서에 걸친 일이라 WikiAdminService가
 * 같은 트랜잭션에서 처리한다.
 */
@Entity
@Table(name = "spaces")
public class Space extends AssignedIdEntity {

    @Id
    private String id;

    private String title;

    @Enumerated(EnumType.STRING)
    @Column(name = "default_classification")
    private Classification defaultClassification;

    @ElementCollection
    @CollectionTable(name = "space_viewers", joinColumns = @JoinColumn(name = "space_id"))
    @Column(name = "principal")
    private Set<String> viewers = new HashSet<>();

    protected Space() {
    }

    public Space(String id, String title, Collection<String> viewers, Classification defaultClassification) {
        this.id = id;
        this.title = title;
        this.viewers.addAll(viewers);
        this.defaultClassification = defaultClassification;
    }

    public void changeViewers(Collection<String> principals) {
        viewers.clear();
        viewers.addAll(principals);
    }

    public void changeDefaultClassification(Classification classification) {
        this.defaultClassification = classification;
    }

    @Override
    public String getId() {
        return id;
    }

    public String getTitle() {
        return title;
    }

    public Classification getDefaultClassification() {
        return defaultClassification;
    }

    public Set<String> getViewers() {
        return Set.copyOf(viewers);
    }
}
