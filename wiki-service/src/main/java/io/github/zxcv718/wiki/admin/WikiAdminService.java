package io.github.zxcv718.wiki.admin;

import io.github.zxcv718.wiki.admin.AdminRequests.CreateDocument;
import io.github.zxcv718.wiki.admin.AdminRequests.DocumentImport;
import io.github.zxcv718.wiki.admin.AdminRequests.GroupImport;
import io.github.zxcv718.wiki.admin.AdminRequests.Import;
import io.github.zxcv718.wiki.admin.AdminRequests.SpaceImport;
import io.github.zxcv718.wiki.admin.AdminRequests.UserImport;
import io.github.zxcv718.wiki.admin.AdminResponses.ImportResult;
import io.github.zxcv718.wiki.admin.AdminResponses.SpaceState;
import io.github.zxcv718.wiki.admin.AdminResponses.UserState;
import io.github.zxcv718.wiki.config.WikiProperties;
import io.github.zxcv718.wiki.domain.Classification;
import io.github.zxcv718.wiki.domain.Document;
import io.github.zxcv718.wiki.domain.DocumentRepository;
import io.github.zxcv718.wiki.domain.Group;
import io.github.zxcv718.wiki.domain.GroupRepository;
import io.github.zxcv718.wiki.domain.Space;
import io.github.zxcv718.wiki.domain.SpaceRepository;
import io.github.zxcv718.wiki.domain.User;
import io.github.zxcv718.wiki.domain.UserRepository;
import io.github.zxcv718.wiki.outbox.OutboxEvent;
import io.github.zxcv718.wiki.outbox.OutboxEvent.EventType;
import io.github.zxcv718.wiki.outbox.OutboxRepository;
import io.github.zxcv718.wiki.outbox.TraceParent;
import io.github.zxcv718.wiki.web.ApiException;
import io.github.zxcv718.wiki.web.DocumentState;
import io.github.zxcv718.wiki.web.DocumentTombstone;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.util.List;
import java.util.function.Consumer;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 문서·권한·멤버십을 바꾸는 유일한 경로.
 *
 * 모든 메서드가 한 트랜잭션에서 변경과 아웃박스 기록을 함께 한다 (ADR-09). 이벤트를 커밋 뒤에 따로 보내면, 그 사이에
 * 장애가 났을 때 문서는 바뀌었는데 인덱스는 모르는 상태가 남는다.
 */
@Service
@Transactional
public class WikiAdminService {

    private final SpaceRepository spaces;
    private final GroupRepository groups;
    private final UserRepository users;
    private final DocumentRepository documents;
    private final OutboxRepository outbox;
    private final WikiProperties properties;
    private final PasswordEncoder passwordEncoder;
    private final TraceParent traceParent;
    private final Clock clock;

    public WikiAdminService(SpaceRepository spaces, GroupRepository groups, UserRepository users,
                            DocumentRepository documents, OutboxRepository outbox, WikiProperties properties,
                            PasswordEncoder passwordEncoder, TraceParent traceParent, Clock clock) {
        this.spaces = spaces;
        this.groups = groups;
        this.users = users;
        this.documents = documents;
        this.outbox = outbox;
        this.properties = properties;
        this.passwordEncoder = passwordEncoder;
        this.traceParent = traceParent;
        this.clock = clock;
    }

    /**
     * 가상 위키를 처음 옮길 때만 쓴다. 문서가 하나라도 있으면 거절한다. 이미 쓰던 위키에 덮어쓰면 revision이 뒤로
     * 돌아가, 인덱서가 새 이벤트를 "이미 반영됨"으로 버릴 수 있다.
     */
    public ImportResult importWiki(Import request) {
        if (documents.count() > 0) {
            throw ApiException.conflict("이미 문서가 있어 가져올 수 없습니다. 가져오기는 빈 위키에 처음 옮길 때만 씁니다.");
        }
        for (SpaceImport s : request.spaces()) {
            spaces.save(new Space(s.space(), s.title(), s.principals(), Classification.fromValue(s.classification())));
        }
        for (GroupImport g : request.groups()) {
            groups.save(new Group(g.group(), g.name()));
        }
        for (UserImport u : request.users()) {
            u.groups().forEach(this::requireGroup);
            users.save(new User(u.userId(), u.name(), u.groups()));
        }
        for (DocumentImport d : request.documents()) {
            Document document = Document.imported(d.docId(), requireSpace(d.space()), d.title(), d.body(),
                    d.version(), d.revision(), d.updatedAt().toInstant(), d.restrictedPrincipals(),
                    classificationOrNull(d.classification()), clock.instant());
            documents.save(document);
            recordEvent(document, EventType.CONTENT_CHANGED);
        }
        return new ImportResult(request.spaces().size(), request.groups().size(), request.users().size(),
                request.documents().size());
    }

    public DocumentState createDocument(CreateDocument request) {
        // 삭제된 문서의 id도 다시 쓰지 않는다. 새 문서는 revision 1부터 시작하는데, 인덱서는 삭제 때의 더 큰 revision을
        // 기억하고 있어 새 문서의 이벤트를 옛 이벤트로 보고 버린다
        if (documents.existsById(request.docId())) {
            throw ApiException.conflict("이미 있는 문서 id입니다. 삭제된 문서의 id도 다시 쓸 수 없습니다: " + request.docId());
        }
        Space space = spaces.findForShare(request.space())
                .orElseThrow(() -> ApiException.notFound("스페이스를 찾을 수 없습니다: " + request.space()));
        Document document = Document.create(request.docId(), space, request.title(), request.body(),
                request.restrictedPrincipals(), classificationOrNull(request.classification()), clock.instant());
        documents.save(document);
        recordEvent(document, EventType.CONTENT_CHANGED);
        return DocumentState.of(document, properties);
    }

    public DocumentState editContent(String docId, String title, String body, int baseVersion) {
        Document document = lockLiveDocument(docId);
        document.editContent(title, body, baseVersion, clock.instant());
        recordEvent(document, EventType.CONTENT_CHANGED);
        return DocumentState.of(document, properties);
    }

    public DocumentState changeRestrictions(String docId, List<String> principals) {
        Document document = lockLiveDocument(docId);
        document.changeRestrictions(principals);
        recordEvent(document, EventType.ACL_CHANGED);
        return DocumentState.of(document, properties);
    }

    public DocumentState changeClassification(String docId, String classification) {
        Document document = lockLiveDocument(docId);
        document.changeClassification(classificationOrNull(classification));
        recordEvent(document, EventType.ACL_CHANGED);
        return DocumentState.of(document, properties);
    }

    public DocumentTombstone deleteDocument(String docId) {
        Document document = lockLiveDocument(docId);
        document.delete();
        recordEvent(document, EventType.DELETED);
        return DocumentTombstone.of(document);
    }

    public SpaceState changeSpaceViewers(String spaceId, List<String> principals) {
        return changeSpace(spaceId, space -> space.changeViewers(principals));
    }

    public SpaceState changeSpaceClassification(String spaceId, String classification) {
        return changeSpace(spaceId, space -> space.changeDefaultClassification(Classification.fromValue(classification)));
    }

    /**
     * 스페이스의 보기 권한이나 기본 등급을 바꾸고, 삭제되지 않은 소속 문서마다 revision을 올려 ACL_CHANGED를 낸다
     * (4장 "권한 변경 전파"). 문서 권한은 인덱스에 문서별로 들어 있어서, 스페이스 하나를 바꿔도 문서마다 다시 써야 한다.
     * 스페이스를 먼저 잠가 같은 스페이스를 바꾸는 요청끼리 차례를 정하고, 문서는 id 순으로 잠근다.
     */
    private SpaceState changeSpace(String spaceId, Consumer<Space> change) {
        Space space = spaces.findForUpdate(spaceId)
                .orElseThrow(() -> ApiException.notFound("스페이스를 찾을 수 없습니다: " + spaceId));
        change.accept(space);
        List<Document> affected = documents.findLiveInSpaceForUpdate(space);
        for (Document document : affected) {
            document.spaceAccessChanged();
            recordEvent(document, EventType.ACL_CHANGED);
        }
        return SpaceState.of(space, affected.size());
    }

    /** 사용자를 만들거나 이름만 바꾼다. 멤버십이 그대로라 이벤트는 없다. */
    public UserState putUser(String userId, String name) {
        User user = users.findForUpdate(userId).orElse(null);
        if (user == null) {
            user = new User(userId, name, List.of());
            users.save(user);
        } else {
            user.rename(name);
        }
        return UserState.of(user);
    }

    /**
     * 로그인 비밀번호를 정한다(ADR-24). bcrypt는 72바이트 뒤를 버리므로, 그보다 긴 값은 뒷부분이 달라도 같은 비밀번호로
     * 통하게 된다. 조용히 잘라 저장하지 않고 거절한다. 멤버십과 무관해 이벤트는 없다.
     */
    public void setPassword(String userId, String password) {
        if (password.getBytes(StandardCharsets.UTF_8).length > 72) {
            throw ApiException.badRequest("password: UTF-8로 72바이트 이하여야 합니다.");
        }
        String hash = passwordEncoder.encode(password);
        lockUser(userId).changePasswordHash(hash);
    }

    /**
     * 멤버십 변경은 문서 revision을 올리지 않는다. 멤버십은 인덱스에 없고 검색할 때 해석하므로(ADR-08), 검색 서버의
     * 그룹 캐시만 무효화하면 된다. 이미 속해 있으면 바뀐 게 없어 이벤트도 내지 않는다.
     */
    public UserState addMember(String groupId, String userId) {
        requireGroup(groupId);
        User user = lockUser(userId);
        if (user.joinGroup(groupId)) {
            outbox.save(OutboxEvent.membershipChanged(userId, clock.instant(), traceParent.current()));
        }
        return UserState.of(user);
    }

    public UserState removeMember(String groupId, String userId) {
        requireGroup(groupId);
        User user = lockUser(userId);
        if (user.leaveGroup(groupId)) {
            outbox.save(OutboxEvent.membershipChanged(userId, clock.instant(), traceParent.current()));
        }
        return UserState.of(user);
    }

    private void recordEvent(Document document, EventType type) {
        outbox.save(OutboxEvent.forDocument(document, type, clock.instant(), traceParent.current()));
    }

    /** 삭제된 문서는 고칠 수 없는 문서로 본다. 이미 삭제된 문서를 다시 삭제해도 404다. */
    private Document lockLiveDocument(String docId) {
        return documents.findForUpdate(docId)
                .filter(d -> !d.isDeleted())
                .orElseThrow(() -> ApiException.notFound("문서를 찾을 수 없습니다: " + docId));
    }

    private Space requireSpace(String spaceId) {
        return spaces.findById(spaceId)
                .orElseThrow(() -> ApiException.notFound("스페이스를 찾을 수 없습니다: " + spaceId));
    }

    private void requireGroup(String groupId) {
        if (groups.findById(groupId).isEmpty()) {
            throw ApiException.notFound("그룹을 찾을 수 없습니다: " + groupId);
        }
    }

    private User lockUser(String userId) {
        return users.findForUpdate(userId)
                .orElseThrow(() -> ApiException.notFound("사용자를 찾을 수 없습니다: " + userId));
    }

    private static Classification classificationOrNull(String value) {
        return value == null ? null : Classification.fromValue(value);
    }
}
