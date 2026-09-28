package io.github.zxcv718.wiki.domain;

import jakarta.persistence.CollectionTable;
import jakarta.persistence.Column;
import jakarta.persistence.ElementCollection;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.Table;
import java.util.Collection;
import java.util.HashSet;
import java.util.Set;

/**
 * 사용자와 그룹 멤버십.
 *
 * 멤버십은 검색 인덱스에 넣지 않고 검색할 때 위키에서 읽는다(ADR-08). 그래서 멤버십이 바뀌어도 문서 revision은
 * 오르지 않고, 검색 서버의 그룹 캐시를 무효화하는 이벤트만 낸다.
 */
@Entity
@Table(name = "users")
public class User extends AssignedIdEntity {

    @Id
    private String id;

    private String name;

    /** bcrypt 해시. 없으면 로그인할 수 없다(ADR-24). */
    private String passwordHash;

    @ElementCollection
    @CollectionTable(name = "group_members", joinColumns = @JoinColumn(name = "user_id"))
    @Column(name = "group_id")
    private Set<String> groupIds = new HashSet<>();

    protected User() {
    }

    public User(String id, String name, Collection<String> groupIds) {
        this.id = id;
        this.name = name;
        this.groupIds.addAll(groupIds);
    }

    public void rename(String name) {
        this.name = name;
    }

    public void changePasswordHash(String passwordHash) {
        this.passwordHash = passwordHash;
    }

    /** 멤버십이 실제로 바뀌었으면 true. 바뀐 게 없으면 캐시 무효화 이벤트를 낼 필요가 없다. */
    public boolean joinGroup(String groupId) {
        return groupIds.add(groupId);
    }

    public boolean leaveGroup(String groupId) {
        return groupIds.remove(groupId);
    }

    @Override
    public String getId() {
        return id;
    }

    public String getName() {
        return name;
    }

    public String getPasswordHash() {
        return passwordHash;
    }

    public Set<String> getGroupIds() {
        return Set.copyOf(groupIds);
    }
}
