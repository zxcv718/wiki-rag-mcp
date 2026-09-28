package io.github.zxcv718.wiki;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ThreadLocalRandom;
import org.junit.jupiter.api.BeforeEach;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.webmvc.test.autoconfigure.MockMvcBuilderCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.data.redis.core.RedisCallback;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

/**
 * PostgreSQL과 Redis를 컨테이너로 띄운 통합 테스트의 공통 부분. 테스트 클래스들이 같은 설정을 써서 스프링 컨텍스트와
 * 컨테이너를 한 번만 띄운다.
 *
 * 폴러 스케줄은 끄고 테스트가 OutboxRelay를 직접 부른다. 켜 두면 테스트가 아웃박스를 확인하기 전에 발행돼 버린다.
 */
@SpringBootTest(properties = {
        "wiki.service-token=" + IntegrationTest.SERVICE_TOKEN,
        "wiki.admin-token=" + IntegrationTest.ADMIN_TOKEN,
        "wiki.outbox.relay.enabled=false",
        // 사용자별 문서 조회가 경우마다 같은 수의 SQL을 실행하는지 세려고 켠다 (InternalApiTest)
        "spring.jpa.properties.hibernate.generate_statistics=true",
        // 토큰 대상이 둘일 때 코드 교환에서 다른 대상으로 바꾸지 못하는지 본다 (AuthorizationServerTest)
        "wiki.auth.resources=" + IntegrationTest.RESOURCE + "," + IntegrationTest.OTHER_RESOURCE,
        "logging.level.org.hibernate.engine.internal.StatisticalLoggingSessionEventListener=WARN"})
@AutoConfigureMockMvc
@Import({TestcontainersConfiguration.class, FakeClientMetadata.class, IntegrationTest.RandomClientAddress.class})
abstract class IntegrationTest {

    static final String SERVICE_TOKEN = "test-service-token";
    static final String ADMIN_TOKEN = "test-admin-token";
    static final String RESOURCE = "http://127.0.0.1:8000/mcp";
    static final String OTHER_RESOURCE = "http://127.0.0.1:9000/mcp";
    /** 서버에서 도는 기밀 클라이언트(데모 에이전트 자리). 설정에는 비밀의 bcrypt 해시만 둔다. */
    static final String DEMO_CLIENT = "wiki-rag-demo";
    static final String DEMO_SECRET = "test-demo-secret";
    static final String DEMO_REDIRECT = "https://demo.example/callback";

    /**
     * 사전 등록 클라이언트. 기본 설정에는 없으므로(운영에서 저절로 살아 있지 않게) 테스트에서 넣는다. 공개 클라이언트
     * wiki-rag-dev는 로컬 compose와 같은 값이고, wiki-rag-demo는 사내 등급의 기밀 클라이언트다.
     */
    @DynamicPropertySource
    static void preRegisteredClients(DynamicPropertyRegistry registry) {
        String secretHash = new BCryptPasswordEncoder().encode(DEMO_SECRET);
        registry.add("wiki.auth.clients[0].id", () -> "wiki-rag-dev");
        registry.add("wiki.auth.clients[0].redirect-uris[0]", () -> "http://127.0.0.1/callback");
        registry.add("wiki.auth.clients[1].id", () -> DEMO_CLIENT);
        registry.add("wiki.auth.clients[1].redirect-uris[0]", () -> DEMO_REDIRECT);
        registry.add("wiki.auth.clients[1].secret-hash", () -> secretHash);
        registry.add("wiki.auth.clients[1].tier", () -> "internal");
    }

    /**
     * 요청마다 다른 클라이언트 IP를 준다. 인가 서버는 IP마다 로그인 실패와 인가 요청 수를 세므로(README "남용 방지"),
     * 테스트들이 같은 IP를 쓰면 1분 안에 한도에 닿아 서로 영향을 준다. 한도를 시험하는 테스트는 IP를 직접 정한다.
     */
    @TestConfiguration(proxyBeanMethods = false)
    static class RandomClientAddress {

        @Bean
        MockMvcBuilderCustomizer randomRemoteAddress() {
            return builder -> builder.defaultRequest(get("/").with(request -> {
                ThreadLocalRandom random = ThreadLocalRandom.current();
                request.setRemoteAddr("10." + random.nextInt(256) + "." + random.nextInt(256) + "."
                        + random.nextInt(1, 255));
                return request;
            }));
        }
    }

    /**
     * 가상 위키(data/wiki)를 줄인 데이터. 권한 테스트에 필요한 경우를 담았다.
     * - infra-011: 인프라 스페이스 + DBA 제한. seoyeon은 스페이스 멤버지만 DBA가 아니고, minjun은 DBA지만 스페이스
     *   멤버가 아니다(ADR-21의 두 방향). taeyang만 둘 다 만족한다.
     * - eng-022: 제한 목록이 빈 문서, mig-001: 보기 권한이 빈 스페이스의 문서. 둘 다 아무도 볼 수 없다.
     * - sec-001: 기밀 스페이스의 문서를 일반으로 적어도 기밀로 남는다(ADR-17).
     */
    static final String FIXTURE = """
            {
              "spaces": [
                {"space": "company", "title": "전사 공지", "principals": ["all"], "classification": "general"},
                {"space": "engineering", "title": "개발", "principals": ["group:eng"], "classification": "general"},
                {"space": "infra", "title": "플랫폼·SRE", "principals": ["group:infra", "group:eng"], "classification": "general"},
                {"space": "security", "title": "정보보안", "principals": ["group:security", "group:leads"], "classification": "confidential"},
                {"space": "migration", "title": "구 위키 이관", "principals": [], "classification": "general"}
              ],
              "groups": [
                {"group": "employees", "name": "전 직원"},
                {"group": "eng", "name": "개발본부"},
                {"group": "infra", "name": "플랫폼·SRE팀"},
                {"group": "dba", "name": "DB 관리자"},
                {"group": "data", "name": "데이터팀"},
                {"group": "security", "name": "정보보안팀"},
                {"group": "leads", "name": "경영진·팀장"}
              ],
              "users": [
                {"user_id": "seoyeon", "name": "박서연", "groups": ["employees", "eng", "infra"]},
                {"user_id": "taeyang", "name": "강태양", "groups": ["employees", "eng", "infra", "dba"]},
                {"user_id": "minjun", "name": "이민준", "groups": ["employees", "data", "dba"]},
                {"user_id": "gaeun", "name": "문가은", "groups": ["employees"]}
              ],
              "documents": [
                {"doc_id": "co-001", "space": "company", "title": "회사 소개", "body": "# 회사 소개\\n새솔소프트",
                 "version": 2, "revision": 2, "updated_at": "2026-03-02T09:00:00+09:00"},
                {"doc_id": "eng-022", "space": "engineering", "title": "사내 해커톤 운영 안내", "body": "# 해커톤",
                 "version": 6, "revision": 9, "updated_at": "2025-10-13T11:56:00+09:00", "restricted_principals": []},
                {"doc_id": "infra-001", "space": "infra", "title": "배포 절차", "body": "# 배포",
                 "version": 1, "revision": 1, "updated_at": "2026-05-01T00:00:00Z", "restricted_principals": ["all"]},
                {"doc_id": "infra-011", "space": "infra", "title": "대용량 테이블 마이그레이션 절차", "body": "# 마이그레이션",
                 "version": 4, "revision": 5, "updated_at": "2026-02-02T15:35:00+09:00",
                 "restricted_principals": ["group:dba"], "classification": "confidential"},
                {"doc_id": "mig-001", "space": "migration", "title": "구 위키 안내", "body": "# 이관",
                 "version": 1, "revision": 1, "updated_at": "2024-01-01T00:00:00Z"},
                {"doc_id": "sec-001", "space": "security", "title": "접근 재인증", "body": "# 재인증",
                 "version": 3, "revision": 4, "updated_at": "2026-06-01T00:00:00Z", "classification": "general"}
              ]
            }
            """;

    static final int FIXTURE_DOCUMENTS = 6;

    @Autowired
    MockMvc mvc;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    StringRedisTemplate redis;

    @BeforeEach
    void resetStores() {
        jdbc.execute("TRUNCATE outbox, document_restrictions, documents, space_viewers, spaces, group_members, users,"
                + " groups, oauth2_authorization, oauth2_authorization_consent, oauth2_rotated_refresh_token"
                + " RESTART IDENTITY");
        redis.execute((RedisCallback<Void>) connection -> {
            connection.serverCommands().flushDb();
            return null;
        });
    }

    void importFixture() throws Exception {
        asAdmin(post("/admin/import").contentType(MediaType.APPLICATION_JSON).content(FIXTURE))
                .andExpect(status().isOk());
    }

    ResultActions asService(MockHttpServletRequestBuilder request) throws Exception {
        return mvc.perform(request.header(HttpHeaders.AUTHORIZATION, "Bearer " + SERVICE_TOKEN));
    }

    ResultActions asAdmin(MockHttpServletRequestBuilder request) throws Exception {
        return mvc.perform(request.header(HttpHeaders.AUTHORIZATION, "Bearer " + ADMIN_TOKEN));
    }

    ResultActions getAsService(String path) throws Exception {
        return asService(get(path));
    }

    /** 아웃박스 행을 id 순으로 (aggregate_id, revision, event_type)만 뽑는다. */
    List<Map<String, Object>> outboxRows() {
        return jdbc.queryForList("SELECT aggregate_id, revision, event_type FROM outbox ORDER BY id");
    }

    long documentRevision(String docId) {
        return jdbc.queryForObject("SELECT revision FROM documents WHERE id = ?", Long.class, docId);
    }
}
