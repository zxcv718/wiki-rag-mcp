package io.github.zxcv718.wiki.domain;

import jakarta.persistence.CollectionTable;
import jakarta.persistence.Column;
import jakarta.persistence.ElementCollection;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 위키 문서.
 *
 * 번호 두 개의 규칙을 이 클래스가 정한다 (ADR-19).
 * - version: 내용 버전. 제목이나 본문이 바뀔 때만 오른다. 검색 결과의 출처 표기에 쓴다.
 * - revision: 내용·권한·등급이 바뀌거나 삭제될 때마다 1 오른다. 인덱서가 이벤트 순서를 이 값으로 판단한다.
 *
 * version만으로 순서를 판단하면 같은 버전에서 권한을 줬다가 회수할 때 회수 이벤트가 중복으로 버려진다.
 * 문서를 고치는 쪽은 행 잠금(DocumentRepository.findForUpdate)을 잡은 뒤 이 메서드들을 불러, 동시에 고쳐도
 * revision이 겹치지 않게 한다.
 */
@Entity
@Table(name = "documents")
public class Document extends AssignedIdEntity {

    @Id
    private String id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "space_id")
    private Space space;

    private String title;

    private String body;

    private int version;

    private long revision;

    // null이면 스페이스 기본 등급을 따른다
    @Enumerated(EnumType.STRING)
    private Classification classification;

    private boolean deleted;

    private Instant createdAt;

    // 내용이 바뀐 시각. 권한·등급 변경으로는 바뀌지 않는다("최근 바뀐 문서"는 내용 변경을 뜻함)
    private Instant updatedAt;

    @ElementCollection
    @CollectionTable(name = "document_restrictions", joinColumns = @JoinColumn(name = "document_id"))
    @Column(name = "principal")
    private Set<String> restrictions = new HashSet<>();

    protected Document() {
    }

    private Document(String id, Space space, String title, String body, int version, long revision,
                     Collection<String> restrictions, Classification classification, Instant createdAt,
                     Instant updatedAt) {
        this.id = id;
        this.space = space;
        this.title = title;
        this.body = body;
        this.version = version;
        this.revision = revision;
        this.restrictions.addAll(restrictions);
        this.classification = classification;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    /** 새 문서. 제한을 따로 주지 않으면 제한 없는 문서(["all"])다. */
    public static Document create(String id, Space space, String title, String body,
                                  Collection<String> restrictions, Classification classification, Instant now) {
        return new Document(id, space, title, body, 1, 1, orUnrestricted(restrictions), classification, now, now);
    }

    /**
     * 가상 위키에서 옮겨 온 문서. version, revision, updated_at을 그대로 옮겨 골든셋과 평가 결과가 계속 맞게 한다.
     * 제한을 빼면 제한 없는 문서지만, 빈 제한 목록은 그대로 둔다. 빈 목록은 아무도 볼 수 없는 문서라는 뜻이다.
     */
    public static Document imported(String id, Space space, String title, String body, int version, long revision,
                                    Instant updatedAt, Collection<String> restrictions,
                                    Classification classification, Instant now) {
        return new Document(id, space, title, body, version, revision, orUnrestricted(restrictions), classification,
                now, updatedAt);
    }

    private static Collection<String> orUnrestricted(Collection<String> restrictions) {
        return restrictions == null ? List.of(AccessPolicy.ALL) : restrictions;
    }

    /** 내용 수정. 읽은 뒤 다른 사람이 먼저 고쳤다면(base_version이 다르면) 덮어쓰지 않는다. */
    public void editContent(String title, String body, int baseVersion, Instant now) {
        if (baseVersion != version) {
            throw new VersionConflictException(id, version, baseVersion);
        }
        this.title = title;
        this.body = body;
        this.version++;
        this.revision++;
        this.updatedAt = now;
    }

    public void changeRestrictions(Collection<String> principals) {
        restrictions.clear();
        restrictions.addAll(principals);
        revision++;
    }

    public void changeClassification(Classification classification) {
        this.classification = classification;
        revision++;
    }

    /** 행을 지우지 않고 표시만 한다. 삭제된 문서의 revision을 알아야 늦게 도착한 옛 이벤트를 버릴 수 있다. */
    public void delete() {
        deleted = true;
        revision++;
    }

    /**
     * 스페이스의 보기 권한이나 기본 등급이 바뀌었다. 문서 자체는 그대로지만 검색 인덱스의 권한·등급 필드를 다시
     * 써야 하므로 revision을 올린다.
     */
    public void spaceAccessChanged() {
        revision++;
    }

    public boolean isVisibleTo(Set<String> principals) {
        return AccessPolicy.canView(principals, space.getViewers(), restrictions);
    }

    public Classification effectiveClassification() {
        return Classification.effective(space.getDefaultClassification(), classification);
    }

    @Override
    public String getId() {
        return id;
    }

    public Space getSpace() {
        return space;
    }

    public String getTitle() {
        return title;
    }

    public String getBody() {
        return body;
    }

    public int getVersion() {
        return version;
    }

    public long getRevision() {
        return revision;
    }

    public Classification getClassification() {
        return classification;
    }

    public boolean isDeleted() {
        return deleted;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }

    public Set<String> getRestrictions() {
        return Set.copyOf(restrictions);
    }
}
