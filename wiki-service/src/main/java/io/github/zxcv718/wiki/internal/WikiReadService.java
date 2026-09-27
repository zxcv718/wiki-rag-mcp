package io.github.zxcv718.wiki.internal;

import io.github.zxcv718.wiki.config.WikiProperties;
import io.github.zxcv718.wiki.domain.AccessPolicy;
import io.github.zxcv718.wiki.domain.Document;
import io.github.zxcv718.wiki.domain.DocumentRepository;
import io.github.zxcv718.wiki.domain.SpaceRepository;
import io.github.zxcv718.wiki.domain.User;
import io.github.zxcv718.wiki.domain.UserRepository;
import io.github.zxcv718.wiki.internal.InternalResponses.DocumentPage;
import io.github.zxcv718.wiki.internal.InternalResponses.RevisionList;
import io.github.zxcv718.wiki.internal.InternalResponses.SpaceList;
import io.github.zxcv718.wiki.internal.InternalResponses.SpaceSummary;
import io.github.zxcv718.wiki.internal.InternalResponses.UserGroups;
import io.github.zxcv718.wiki.web.ApiException;
import io.github.zxcv718.wiki.web.DocumentState;
import io.github.zxcv718.wiki.web.DocumentView;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

/**
 * 검색 서버와 인덱서가 읽는 조회. 캐시를 거치지 않고 매번 DB에서 읽는다 (ADR-19 "상태는 위키에서 다시 읽음").
 *
 * 요청 하나의 쿼리들이 한 스냅숏을 보도록 REPEATABLE READ로 읽는다. READ COMMITTED는 쿼리마다 새 스냅숏을 잡아서,
 * 멤버십은 회수가 커밋되기 전 값으로, 제한 목록은 그 뒤 변경이 커밋된 값으로 읽는 식으로 한 번도 커밋된 적 없는
 * 조합으로 권한을 판단할 수 있다. PostgreSQL의 REPEATABLE READ는 스냅숏을 트랜잭션 처음에 한 번 잡는 것이라 읽기에
 * 잠금이 더 들지 않고, 쓰지 않는 트랜잭션은 직렬화 실패로 되돌려지지도 않는다.
 */
@Service
@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
public class WikiReadService {

    /**
     * 사용자별 문서 조회에서 없는 문서, 삭제된 문서, 권한 없는 문서, 모르는 사용자에 모두 같은 응답을 준다.
     * 응답이 다르면 권한 없는 사용자도 그 id의 문서가 있다는 사실을 알 수 있다 ("권한 없는 문서는 흔적을 남기지 않음").
     */
    private static final String HIDDEN = "문서를 찾을 수 없습니다.";

    private final SpaceRepository spaces;
    private final DocumentRepository documents;
    private final UserRepository users;
    private final WikiProperties properties;

    public WikiReadService(SpaceRepository spaces, DocumentRepository documents, UserRepository users,
                           WikiProperties properties) {
        this.spaces = spaces;
        this.documents = documents;
        this.users = users;
        this.properties = properties;
    }

    public SpaceList spaces() {
        return new SpaceList(spaces.findAllByOrderByIdAsc().stream()
                .map(s -> new SpaceSummary(s.getId(), s.getTitle()))
                .toList());
    }

    public DocumentPage documents(String after, int limit) {
        // 한 개 더 읽어 다음 페이지가 있는지 본다. 마지막 페이지에서 next가 null이 되어 빈 페이지를 한 번 더 부르지 않는다
        List<Document> rows = documents.findByDeletedFalseAndIdGreaterThanOrderByIdAsc(
                after == null ? "" : after, Limit.of(limit + 1));
        boolean hasMore = rows.size() > limit;
        List<DocumentState> page = rows.stream().limit(limit).map(d -> DocumentState.of(d, properties)).toList();
        String next = hasMore ? page.getLast().docId() : null;
        return new DocumentPage(page, next);
    }

    public DocumentView document(String docId) {
        Document document = documents.findWithAccess(docId)
                .orElseThrow(() -> ApiException.notFound("문서를 찾을 수 없습니다: " + docId));
        return DocumentView.of(document, properties);
    }

    public RevisionList revisions() {
        return new RevisionList(documents.findAllRevisions());
    }

    public UserGroups groupsOf(String userId) {
        User user = users.findWithGroups(userId)
                .orElseThrow(() -> ApiException.notFound("사용자를 찾을 수 없습니다: " + userId));
        List<String> groups = user.getGroupIds().stream().map(g -> "group:" + g).sorted().toList();
        return new UserGroups(user.getId(), groups);
    }

    /**
     * get_document와 문서 리소스의 권한 재확인 (ADR-07). 스페이스 권한과 문서 제한을 둘 다 따진다 (ADR-21).
     *
     * 사용자와 문서를 결과와 관계없이 둘 다 읽고, 권한 판단에 필요한 목록은 그 두 쿼리에서 함께 가져온다. 그래서 숨기는
     * 네 경우와 보여 주는 경우 모두 같은 쿼리 두 개만 실행해, 응답 시간으로도 문서가 있는지 알 수 없다.
     */
    public DocumentState documentFor(String userId, String docId) {
        Optional<User> user = users.findWithGroups(userId);
        Optional<Document> document = documents.findWithAccess(docId).filter(d -> !d.isDeleted());
        if (user.isEmpty() || document.isEmpty()
                || !document.get().isVisibleTo(AccessPolicy.principalsOf(user.get()))) {
            throw ApiException.notFound(HIDDEN);
        }
        return DocumentState.of(document.get(), properties);
    }
}
