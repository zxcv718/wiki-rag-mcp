package io.github.zxcv718.wiki;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.contains;
import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

class AdminApiTest extends IntegrationTest {

    @BeforeEach
    void setUp() throws Exception {
        importFixture();
    }

    private ResultActions adminJson(MockHttpServletRequestBuilder request, String json) throws Exception {
        return asAdmin(request.contentType(MediaType.APPLICATION_JSON).content(json));
    }

    private static Map<String, Object> event(String aggregateId, long revision, String type) {
        return Map.of("aggregate_id", aggregateId, "revision", revision, "event_type", type);
    }

    private List<Map<String, Object>> outboxAfterImport() {
        List<Map<String, Object>> rows = outboxRows();
        return rows.subList(FIXTURE_DOCUMENTS, rows.size());
    }

    @Test
    void importWritesOneContentChangedPerDocument() {
        assertThat(outboxRows()).containsExactly(
                event("co-001", 2, "CONTENT_CHANGED"),
                event("eng-022", 9, "CONTENT_CHANGED"),
                event("infra-001", 1, "CONTENT_CHANGED"),
                event("infra-011", 5, "CONTENT_CHANGED"),
                event("mig-001", 1, "CONTENT_CHANGED"),
                event("sec-001", 4, "CONTENT_CHANGED"));
    }

    /** 가져오기 전에 사용자를 먼저 만들어 두면 기본 키가 겹친다. DB가 막은 unique 위반은 409다. */
    @Test
    void importConflictsWithExistingUser() throws Exception {
        resetStores();
        adminJson(put("/admin/users/seoyeon"), "{\"name\": \"박서연\"}").andExpect(status().isOk());

        adminJson(post("/admin/import"), FIXTURE)
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("conflict"));
        assertThat(jdbc.queryForObject("SELECT count(*) FROM documents", Long.class)).isZero();
        assertThat(outboxRows()).isEmpty();
    }

    /** NUL 문자는 PostgreSQL text에 저장할 수 없다(SQLSTATE 22021). 요청을 고쳐야 하는 문제라 409가 아니라 400이다. */
    @Test
    void unstorableBodyIsBadRequest() throws Exception {
        adminJson(post("/admin/documents"), """
                {"doc_id": "infra-200", "space": "infra", "title": "t", "body": "a\\u0000b"}
                """)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("bad_request"));
        getAsService("/internal/documents/infra-200").andExpect(status().isNotFound());
        assertThat(outboxAfterImport()).isEmpty();
    }

    /** 오타 난 권한 필드를 무시하면 제한을 뺀 것으로 읽혀 제한 없는 문서가 만들어진다. 모르는 필드는 거절한다. */
    @Test
    void unknownFieldsAreRejected() throws Exception {
        for (String field : List.of("restrictions", "restrictedPrincipals")) {
            adminJson(post("/admin/documents"), """
                    {"doc_id": "infra-200", "space": "infra", "title": "t", "body": "b", "%s": ["group:dba"]}
                    """.formatted(field))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.error").value("bad_request"))
                    .andExpect(jsonPath("$.message").value("알 수 없는 필드입니다: " + field));
        }
        adminJson(post("/admin/import"), """
                {"spaces": [], "groups": [], "users": [], "documents": [{"doc_id": "x-1", "space": "infra",
                 "title": "t", "body": "b", "version": 1, "revision": 1, "updated_at": "2026-01-01T00:00:00Z",
                 "restrictions": []}]}
                """)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.message").value("알 수 없는 필드입니다: documents[0].restrictions"));
        getAsService("/internal/documents/infra-200").andExpect(status().isNotFound());
        assertThat(outboxAfterImport()).isEmpty();
    }

    /** 등급 키가 빠지거나 이름이 틀리면 null(스페이스 기본값)로 읽지 않고 거절한다. 명시한 null만 등급을 푼다. */
    @Test
    void documentClassificationKeyIsRequired() throws Exception {
        adminJson(put("/admin/documents/infra-011/classification"), "{}")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("bad_request"))
                .andExpect(jsonPath("$.message").value("classification: 값이 빠졌거나 형식이 틀렸습니다."));
        adminJson(put("/admin/documents/infra-011/classification"), "{\"clasification\": \"general\"}")
                .andExpect(status().isBadRequest());
        assertThat(documentRevision("infra-011")).isEqualTo(5);
        getAsService("/internal/documents/infra-011").andExpect(jsonPath("$.classification").value("confidential"));

        adminJson(put("/admin/documents/infra-011/classification"), "{\"classification\": null}")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.revision").value(6))
                .andExpect(jsonPath("$.classification").value("general"));
    }

    @Test
    void importIsRejectedWhenDocumentsExist() throws Exception {
        adminJson(post("/admin/import"), FIXTURE)
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("conflict"));
        assertThat(outboxRows()).hasSize(FIXTURE_DOCUMENTS);
    }

    /** 문서마다 version·revision·updated_at이 README "revision과 version"대로 움직이는지 따라간다. */
    @Test
    void documentLifecycleFollowsVersionAndRevisionRules() throws Exception {
        adminJson(post("/admin/documents"), """
                {"doc_id": "infra-100", "space": "infra", "title": "새 런북", "body": "# 런북"}
                """)
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.version").value(1))
                .andExpect(jsonPath("$.revision").value(1))
                .andExpect(jsonPath("$.restricted_principals").value(contains("all")));
        String createdAt = jdbc.queryForObject(
                "SELECT to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS.US') FROM documents WHERE id = 'infra-100'",
                String.class);

        adminJson(put("/admin/documents/infra-100"), """
                {"title": "새 런북 v2", "body": "# 런북\\n고침", "base_version": 1}
                """)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.version").value(2))
                .andExpect(jsonPath("$.revision").value(2))
                .andExpect(jsonPath("$.title").value("새 런북 v2"));
        String editedAt = jdbc.queryForObject(
                "SELECT to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS.US') FROM documents WHERE id = 'infra-100'",
                String.class);
        assertThat(editedAt).isGreaterThan(createdAt);

        adminJson(put("/admin/documents/infra-100/restrictions"), "{\"principals\": [\"group:dba\", \"user:jiho\"]}")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.version").value(2))
                .andExpect(jsonPath("$.revision").value(3))
                .andExpect(jsonPath("$.restricted_principals").value(contains("group:dba", "user:jiho")));

        adminJson(put("/admin/documents/infra-100/classification"), "{\"classification\": \"confidential\"}")
                .andExpect(jsonPath("$.revision").value(4))
                .andExpect(jsonPath("$.classification").value("confidential"));
        adminJson(put("/admin/documents/infra-100/classification"), "{\"classification\": null}")
                .andExpect(jsonPath("$.revision").value(5))
                .andExpect(jsonPath("$.classification").value("general"));

        asAdmin(delete("/admin/documents/infra-100"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.revision").value(6))
                .andExpect(jsonPath("$.deleted").value(true));

        assertThat(jdbc.queryForObject("SELECT to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS.US') FROM documents"
                + " WHERE id = 'infra-100'", String.class)).isEqualTo(editedAt);
        assertThat(outboxAfterImport()).containsExactly(
                event("infra-100", 1, "CONTENT_CHANGED"),
                event("infra-100", 2, "CONTENT_CHANGED"),
                event("infra-100", 3, "ACL_CHANGED"),
                event("infra-100", 4, "ACL_CHANGED"),
                event("infra-100", 5, "ACL_CHANGED"),
                event("infra-100", 6, "DELETED"));
    }

    @Test
    void permissionChangeKeepsUpdatedAt() throws Exception {
        adminJson(put("/admin/documents/infra-011/restrictions"), "{\"principals\": [\"all\"]}")
                .andExpect(jsonPath("$.revision").value(6))
                .andExpect(jsonPath("$.version").value(4))
                .andExpect(jsonPath("$.updated_at").value("2026-02-02T06:35:00Z"));
        adminJson(put("/admin/documents/infra-011"), "{\"title\": \"t\", \"body\": \"b\", \"base_version\": 4}")
                .andExpect(jsonPath("$.updated_at").value(not("2026-02-02T06:35:00Z")));
    }

    @Test
    void staleBaseVersionIsConflictAndChangesNothing() throws Exception {
        adminJson(put("/admin/documents/infra-011"), "{\"title\": \"t\", \"body\": \"b\", \"base_version\": 3}")
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("conflict"));
        assertThat(documentRevision("infra-011")).isEqualTo(5);
        assertThat(outboxAfterImport()).isEmpty();
    }

    @Test
    void deletedDocumentCannotBeChangedOrRecreated() throws Exception {
        asAdmin(delete("/admin/documents/infra-001")).andExpect(status().isOk());

        asAdmin(delete("/admin/documents/infra-001")).andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("not_found"));
        adminJson(put("/admin/documents/infra-001"), "{\"title\": \"t\", \"body\": \"b\", \"base_version\": 1}")
                .andExpect(status().isNotFound());
        adminJson(post("/admin/documents"), """
                {"doc_id": "infra-001", "space": "infra", "title": "다시", "body": "b"}
                """).andExpect(status().isConflict());
        assertThat(documentRevision("infra-001")).isEqualTo(2);
    }

    @Test
    void missingTargetsAre404() throws Exception {
        adminJson(post("/admin/documents"), """
                {"doc_id": "x-001", "space": "nowhere", "title": "t", "body": "b"}
                """).andExpect(status().isNotFound());
        adminJson(put("/admin/documents/nope-001/restrictions"), "{\"principals\": [\"all\"]}")
                .andExpect(status().isNotFound());
        adminJson(put("/admin/spaces/nowhere/viewers"), "{\"principals\": [\"all\"]}")
                .andExpect(status().isNotFound());
        asAdmin(put("/admin/groups/nobody/members/seoyeon")).andExpect(status().isNotFound());
        asAdmin(put("/admin/groups/dba/members/ghost")).andExpect(status().isNotFound());
    }

    @Test
    void malformedPrincipalOrClassificationIsBadRequest() throws Exception {
        adminJson(put("/admin/documents/infra-011/restrictions"), "{\"principals\": [\"dba\"]}")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("bad_request"));
        adminJson(put("/admin/documents/infra-011/restrictions"), "{\"principals\": [\"group:\"]}")
                .andExpect(status().isBadRequest());
        adminJson(put("/admin/documents/infra-011/restrictions"), "{\"principals\": [null]}")
                .andExpect(status().isBadRequest());
        adminJson(put("/admin/documents/infra-011/classification"), "{\"classification\": \"secret\"}")
                .andExpect(status().isBadRequest());
        adminJson(put("/admin/spaces/infra/classification"), "{\"classification\": null}")
                .andExpect(status().isBadRequest());
        adminJson(put("/admin/documents/infra-011"), "{\"title\": \"t\", \"body\": \"b\"}")
                .andExpect(status().isBadRequest());
        adminJson(put("/admin/documents/infra-011"), "not json").andExpect(status().isBadRequest());
        assertThat(documentRevision("infra-011")).isEqualTo(5);
    }

    /** 점으로만 된 id는 URL 경로에서 경로 이동으로 해석돼 그 문서를 가리킬 수 없으므로 만들지 않는다. */
    @Test
    void dotOnlyDocumentIdIsBadRequest() throws Exception {
        for (String docId : List.of(".", "..", "...")) {
            adminJson(post("/admin/documents"),
                    "{\"doc_id\": \"" + docId + "\", \"space\": \"infra\", \"title\": \"t\", \"body\": \"b\"}")
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.error").value("bad_request"));
        }
        adminJson(post("/admin/documents"),
                "{\"doc_id\": \".hidden\", \"space\": \"infra\", \"title\": \"t\", \"body\": \"b\"}")
                .andExpect(status().isCreated());
    }

    /** 스페이스 권한이 바뀌면 삭제되지 않은 소속 문서마다 revision이 오르고 ACL_CHANGED가 하나씩 나간다. */
    @Test
    void spaceViewerChangeBumpsEveryLiveDocument() throws Exception {
        asAdmin(delete("/admin/documents/infra-001")).andExpect(status().isOk());

        adminJson(put("/admin/spaces/infra/viewers"), "{\"principals\": [\"group:infra\"]}")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.principals").value(contains("group:infra")))
                .andExpect(jsonPath("$.documents_changed").value(1));

        assertThat(documentRevision("infra-011")).isEqualTo(6);
        assertThat(documentRevision("infra-001")).isEqualTo(2);
        assertThat(documentRevision("co-001")).isEqualTo(2);
        assertThat(outboxAfterImport()).containsExactly(
                event("infra-001", 2, "DELETED"),
                event("infra-011", 6, "ACL_CHANGED"));
        getAsService("/internal/documents/infra-011")
                .andExpect(jsonPath("$.space_principals").value(contains("group:infra")))
                .andExpect(jsonPath("$.version").value(4));
    }

    @Test
    void spaceClassificationChangeBumpsEveryLiveDocument() throws Exception {
        adminJson(put("/admin/spaces/infra/classification"), "{\"classification\": \"confidential\"}")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.documents_changed").value(2));

        assertThat(outboxAfterImport()).containsExactly(
                event("infra-001", 2, "ACL_CHANGED"),
                event("infra-011", 6, "ACL_CHANGED"));
        getAsService("/internal/documents/infra-001").andExpect(jsonPath("$.classification").value("confidential"));
    }

    @Test
    void emptySpaceViewersHideEveryDocument() throws Exception {
        adminJson(put("/admin/spaces/infra/viewers"), "{\"principals\": []}").andExpect(status().isOk());
        getAsService("/internal/users/taeyang/documents/infra-011").andExpect(status().isNotFound());
        getAsService("/internal/documents/infra-011").andExpect(jsonPath("$.space_principals").isEmpty());
    }

    /** 멤버십 변경은 문서 revision을 올리지 않고, 실제로 바뀔 때만 캐시 무효화 이벤트를 낸다 (ADR-08). */
    @Test
    void membershipChangeWritesEventWithoutTouchingDocuments() throws Exception {
        getAsService("/internal/users/seoyeon/documents/infra-011").andExpect(status().isNotFound());

        asAdmin(put("/admin/groups/dba/members/seoyeon"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.groups").value(contains("group:dba", "group:employees", "group:eng",
                        "group:infra")));
        asAdmin(put("/admin/groups/dba/members/seoyeon")).andExpect(status().isOk());
        getAsService("/internal/users/seoyeon/documents/infra-011").andExpect(status().isOk());

        asAdmin(delete("/admin/groups/dba/members/seoyeon")).andExpect(status().isOk());
        getAsService("/internal/users/seoyeon/documents/infra-011").andExpect(status().isNotFound());

        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT aggregate_type, aggregate_id, revision, event_type FROM outbox WHERE id > ? ORDER BY id",
                FIXTURE_DOCUMENTS);
        assertThat(rows).hasSize(2).allSatisfy(row -> {
            assertThat(row.get("aggregate_type")).isEqualTo("USER");
            assertThat(row.get("aggregate_id")).isEqualTo("seoyeon");
            assertThat(row.get("revision")).isNull();
            assertThat(row.get("event_type")).isEqualTo("MEMBERSHIP_CHANGED");
        });
        assertThat(documentRevision("infra-011")).isEqualTo(5);
    }

    @Test
    void putUserCreatesOrRenamesWithoutEvent() throws Exception {
        adminJson(put("/admin/users/newbie"), "{\"name\": \"신입\"}")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.user_id").value("newbie"))
                .andExpect(jsonPath("$.groups").isEmpty());
        adminJson(put("/admin/users/newbie"), "{\"name\": \"신입 사원\"}")
                .andExpect(jsonPath("$.name").value("신입 사원"));
        getAsService("/internal/users/newbie/groups").andExpect(jsonPath("$.groups").isEmpty());
        assertThat(outboxAfterImport()).isEmpty();
    }
}
