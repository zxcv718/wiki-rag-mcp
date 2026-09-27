package io.github.zxcv718.wiki;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.contains;
import static org.hamcrest.Matchers.empty;
import static org.hamcrest.Matchers.nullValue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.jayway.jsonpath.JsonPath;
import io.github.zxcv718.wiki.internal.WikiReadService;
import jakarta.persistence.EntityManagerFactory;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.hibernate.SessionFactory;
import org.hibernate.stat.Statistics;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.annotation.AnnotatedElementUtils;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

class InternalApiTest extends IntegrationTest {

    @Autowired
    EntityManagerFactory entityManagerFactory;

    @BeforeEach
    void setUp() throws Exception {
        importFixture();
    }

    @Test
    void spacesAreSortedById() throws Exception {
        getAsService("/internal/spaces")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.spaces[*].space")
                        .value(contains("company", "engineering", "infra", "migration", "security")))
                .andExpect(jsonPath("$.spaces[2].title").value("플랫폼·SRE"));
    }

    @Test
    void documentStateMatchesContract() throws Exception {
        getAsService("/internal/documents/infra-011")
                .andExpect(status().isOk())
                .andExpect(content().json("""
                        {
                          "doc_id": "infra-011", "title": "대용량 테이블 마이그레이션 절차", "space": "infra",
                          "url": "https://wiki.saesol.example/doc/infra-011",
                          "version": 4, "revision": 5, "updated_at": "2026-02-02T06:35:00Z",
                          "body": "# 마이그레이션",
                          "space_principals": ["group:eng", "group:infra"],
                          "restricted_principals": ["group:dba"],
                          "classification": "confidential",
                          "deleted": false
                        }
                        """, true));
    }

    @Test
    void classificationIsHigherOfSpaceDefaultAndDocument() throws Exception {
        getAsService("/internal/documents/sec-001").andExpect(jsonPath("$.classification").value("confidential"));
        getAsService("/internal/documents/infra-001").andExpect(jsonPath("$.classification").value("general"));
    }

    /** 빈 목록은 "아무도 볼 수 없음"이라 ["all"]로 바뀌면 안 된다. 제한을 뺀 문서만 ["all"]이다. */
    @Test
    void importKeepsEmptyListsAndDefaultsMissingRestrictionToAll() throws Exception {
        getAsService("/internal/documents/eng-022")
                .andExpect(jsonPath("$.restricted_principals").value(empty()));
        getAsService("/internal/documents/mig-001")
                .andExpect(jsonPath("$.space_principals").value(empty()))
                .andExpect(jsonPath("$.restricted_principals").value(contains("all")));
    }

    @Test
    void deletedDocumentIsTombstoneAndUnknownDocumentIs404() throws Exception {
        asAdmin(delete("/admin/documents/infra-001")).andExpect(status().isOk());

        getAsService("/internal/documents/infra-001")
                .andExpect(status().isOk())
                .andExpect(content().json("{\"doc_id\": \"infra-001\", \"revision\": 2, \"deleted\": true}", true));
        getAsService("/internal/documents/infra-999")
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("not_found"));
    }

    @Test
    void documentPagesFollowDocIdOrderAndSkipDeleted() throws Exception {
        asAdmin(delete("/admin/documents/infra-001")).andExpect(status().isOk());

        List<String> seen = new ArrayList<>();
        String after = null;
        int pages = 0;
        do {
            String path = "/internal/documents?limit=2" + (after == null ? "" : "&after=" + after);
            MvcResult result = getAsService(path).andExpect(status().isOk()).andReturn();
            List<String> ids = JsonPath.read(body(result), "$.documents[*].doc_id");
            seen.addAll(ids);
            after = JsonPath.read(body(result), "$.next");
            pages++;
        } while (after != null);

        assertThat(seen).containsExactly("co-001", "eng-022", "infra-011", "mig-001", "sec-001");
        assertThat(pages).isEqualTo(3);
    }

    @Test
    void lastPageHasNullNext() throws Exception {
        getAsService("/internal/documents?after=mig-001")
                .andExpect(jsonPath("$.documents[*].doc_id").value(contains("sec-001")))
                .andExpect(jsonPath("$.next").value(nullValue()));
    }

    @Test
    void pageLimitIsChecked() throws Exception {
        getAsService("/internal/documents?limit=0").andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("bad_request"));
        getAsService("/internal/documents?limit=501").andExpect(status().isBadRequest());
        getAsService("/internal/documents?limit=abc").andExpect(status().isBadRequest());
        getAsService("/internal/documents?limit=500").andExpect(status().isOk());
    }

    @Test
    void malformedIdIsBadRequest() throws Exception {
        getAsService("/internal/documents/bad*id")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("bad_request"));
    }

    @Test
    void revisionsIncludeDeletedDocuments() throws Exception {
        asAdmin(delete("/admin/documents/infra-001")).andExpect(status().isOk());

        getAsService("/internal/revisions")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.documents[*].doc_id")
                        .value(contains("co-001", "eng-022", "infra-001", "infra-011", "mig-001", "sec-001")))
                .andExpect(jsonPath("$.documents[2].revision").value(2))
                .andExpect(jsonPath("$.documents[2].deleted").value(true))
                .andExpect(jsonPath("$.documents[3].revision").value(5))
                .andExpect(jsonPath("$.documents[3].deleted").value(false));
    }

    @Test
    void userGroupsArePrincipals() throws Exception {
        getAsService("/internal/users/taeyang/groups")
                .andExpect(status().isOk())
                .andExpect(content().json("""
                        {"user_id": "taeyang", "groups": ["group:dba", "group:employees", "group:eng", "group:infra"]}
                        """, true));
        getAsService("/internal/users/ghost/groups").andExpect(status().isNotFound());
    }

    /** ADR-21의 두 방향과 둘 다 만족하는 경우를 위키의 재확인 경로에서 확인한다. */
    @Test
    void userDocumentChecksBothLayers() throws Exception {
        getAsService("/internal/users/taeyang/documents/infra-011")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.doc_id").value("infra-011"));
        getAsService("/internal/users/seoyeon/documents/infra-011").andExpect(status().isNotFound());
        getAsService("/internal/users/minjun/documents/infra-011").andExpect(status().isNotFound());

        getAsService("/internal/users/seoyeon/documents/infra-001").andExpect(status().isOk());
        getAsService("/internal/users/gaeun/documents/infra-001").andExpect(status().isNotFound());
        getAsService("/internal/users/gaeun/documents/co-001").andExpect(status().isOk());
    }

    @Test
    void emptyPermissionListsHideFromEveryone() throws Exception {
        for (String user : List.of("seoyeon", "taeyang", "minjun", "gaeun")) {
            getAsService("/internal/users/" + user + "/documents/eng-022").andExpect(status().isNotFound());
            getAsService("/internal/users/" + user + "/documents/mig-001").andExpect(status().isNotFound());
        }
    }

    /** 없는 문서, 삭제된 문서, 권한 없는 문서, 모르는 사용자의 응답이 바이트 단위로 같아야 문서의 존재가 드러나지 않는다. */
    @Test
    void hiddenDocumentResponsesAreIdentical() throws Exception {
        asAdmin(delete("/admin/documents/infra-001")).andExpect(status().isOk());

        List<String> bodies = new ArrayList<>();
        for (String path : List.of(
                "/internal/users/taeyang/documents/infra-999",
                "/internal/users/taeyang/documents/infra-001",
                "/internal/users/seoyeon/documents/infra-011",
                "/internal/users/ghost/documents/infra-011")) {
            MvcResult result = getAsService(path).andExpect(status().isNotFound()).andReturn();
            bodies.add(body(result));
        }
        assertThat(bodies).allMatch(b -> b.equals(bodies.getFirst()));
        assertThat(bodies.getFirst()).contains("\"error\":\"not_found\"");
    }

    /**
     * 숨기는 네 경우와 보여 주는 경우가 같은 수의 SQL을 실행해야 응답 시간으로 문서의 존재가 드러나지 않는다.
     * 권한이 없는 문서만 스페이스와 권한 목록을 더 읽으면, 없는 문서보다 느려져 id가 있다는 것이 드러난다.
     */
    @Test
    void hiddenAndVisibleDocumentsRunTheSameStatements() throws Exception {
        asAdmin(delete("/admin/documents/infra-001")).andExpect(status().isOk());
        Statistics statistics = entityManagerFactory.unwrap(SessionFactory.class).getStatistics();
        assertThat(statistics.isStatisticsEnabled()).isTrue();

        Map<String, Long> counts = new LinkedHashMap<>();
        for (String path : List.of(
                "/internal/users/taeyang/documents/infra-999",
                "/internal/users/taeyang/documents/infra-001",
                "/internal/users/seoyeon/documents/infra-011",
                "/internal/users/ghost/documents/infra-011",
                "/internal/users/taeyang/documents/infra-011")) {
            statistics.clear();
            getAsService(path);
            counts.put(path, statistics.getPrepareStatementCount());
        }
        assertThat(counts.values()).containsOnly(2L);
    }

    /** 권한 판단에 쓰는 여러 쿼리가 한 스냅숏을 보도록 읽기 서비스는 REPEATABLE READ로 돈다. */
    @Test
    void readServiceReadsOneSnapshot() {
        Transactional transactional = AnnotatedElementUtils.findMergedAnnotation(WikiReadService.class,
                Transactional.class);
        assertThat(transactional).isNotNull();
        assertThat(transactional.readOnly()).isTrue();
        assertThat(transactional.isolation()).isEqualTo(Isolation.REPEATABLE_READ);
    }

    private static String body(MvcResult result) throws Exception {
        return result.getResponse().getContentAsString(StandardCharsets.UTF_8);
    }
}
