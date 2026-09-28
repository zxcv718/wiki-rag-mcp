package io.github.zxcv718.wiki.auth;

import static org.assertj.core.api.Assertions.assertThat;

import io.github.zxcv718.wiki.auth.ClientMetadataDocuments.Response;
import java.io.IOException;
import java.net.InetAddress;
import java.net.UnknownHostException;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.security.oauth2.core.ClientAuthenticationMethod;
import org.springframework.security.oauth2.server.authorization.client.RegisteredClient;

/**
 * CIMD 문서를 받는 조건(README "클라이언트")을 경우마다 하나씩 확인한다. 이름 해석과 가져오기는 가짜로 바꿔, 네트워크
 * 없이 어떤 주소로 몇 번 가져왔는지 센다.
 */
class ClientMetadataDocumentsTest {

    private static final String ID = "https://app.example/oauth/client.json";
    private static final InetAddress PUBLIC = address("93.184.216.34");

    private final MutableClock clock = new MutableClock(Instant.parse("2026-09-28T00:00:00Z"));
    private List<InetAddress> resolved = List.of(PUBLIC);
    private Response response = json(document(ID, ""));
    private IOException failure;
    private final List<InetAddress> connectedTo = Collections.synchronizedList(new ArrayList<>());
    private final List<Duration> timeouts = new ArrayList<>();
    private final List<ClientMetadataDocuments> opened = new ArrayList<>();

    @AfterEach
    void closeWorkers() {
        opened.forEach(ClientMetadataDocuments::close);
    }

    private ClientMetadataDocuments documents(boolean allowHttpLocalhost) {
        return documents(allowHttpLocalhost, (uri, address, timeout, maxBytes) -> {
            connectedTo.add(address);
            timeouts.add(timeout);
            if (failure != null) {
                throw failure;
            }
            return response;
        });
    }

    private ClientMetadataDocuments documents(boolean allowHttpLocalhost, ClientMetadataDocuments.Fetcher fetcher) {
        ClientMetadataDocuments documents = new ClientMetadataDocuments(host -> resolved, fetcher, allowHttpLocalhost,
                clock);
        opened.add(documents);
        return documents;
    }

    private RegisteredClient find(String clientId) {
        return documents(false).find(clientId);
    }

    private static String document(String clientId, String extraFields) {
        return """
                {"client_id": "%s", "client_name": "문서가 밝힌 이름",
                 "redirect_uris": ["http://localhost/callback"]%s}
                """.formatted(clientId, extraFields);
    }

    private static Response json(String body) {
        return new Response(200, "application/json; charset=utf-8", body.getBytes(StandardCharsets.UTF_8));
    }

    @Test
    void acceptsValidDocumentAsPublicClient() {
        RegisteredClient client = find(ID);

        assertThat(client).isNotNull();
        assertThat(client.getClientId()).isEqualTo(ID);
        assertThat(client.getClientName()).isEqualTo("문서가 밝힌 이름");
        assertThat(client.getRedirectUris()).containsExactly("http://localhost/callback");
        assertThat(client.getClientAuthenticationMethods()).containsExactly(ClientAuthenticationMethod.NONE);
        assertThat(client.getClientSettings().isRequireProofKey()).isTrue();
        assertThat(timeouts.getFirst()).isPositive().isLessThanOrEqualTo(Duration.ofSeconds(3));
    }

    /** 검사를 마친 주소로만 붙는다. 여러 주소가 나와도 모두 공인이어야 하고, 연결은 그중 하나로 한 번만 한다. */
    @Test
    void connectsOnlyToCheckedAddress() {
        InetAddress other = address("93.184.216.35");
        resolved = List.of(PUBLIC, other);

        assertThat(find(ID)).isNotNull();
        assertThat(connectedTo).containsExactly(PUBLIC);
    }

    @Test
    void rejectsPlainHttp() {
        assertThat(find("http://app.example/oauth/client.json")).isNull();
        assertThat(connectedTo).isEmpty();
    }

    @Test
    void httpLocalhostOnlyWhenAllowedAndOnlyOnLoopback() {
        String local = "http://localhost:3000/client.json";
        response = json(document(local, ""));
        resolved = List.of(address("127.0.0.1"));

        assertThat(documents(false).find(local)).isNull();
        assertThat(documents(true).find(local)).isNotNull();

        resolved = List.of(PUBLIC);
        assertThat(documents(true).find(local)).isNull();
        assertThat(documents(true).find("http://localhost.app.example/client.json")).isNull();
    }

    @Test
    void rejectsFragmentUserInfoAndDotSegments() {
        for (String id : List.of(ID + "#x", "https://user@app.example/client.json", "https://app.example/a/../client.json",
                "https://app.example/./client.json", "https://app.example/a/%2e%2e/client.json", "https://app.example/a b")) {
            response = json(document(id, ""));
            assertThat(find(id)).as(id).isNull();
        }
        assertThat(connectedTo).isEmpty();
    }

    @Test
    void rejectsPrivateLoopbackAndLinkLocalAddresses() {
        for (String ip : List.of("10.0.0.1", "127.0.0.1", "169.254.169.254", "172.16.0.1", "192.168.1.1", "100.64.0.1",
                "0.0.0.0", "::1", "fc00::1", "fe80::1", "64:ff9b::a00:1", "2002:a00:1::")) {
            resolved = List.of(address(ip));
            assertThat(find(ID)).as(ip).isNull();
        }
        assertThat(connectedTo).isEmpty();
    }

    @Test
    void rejectsWhenAnyResolvedAddressIsPrivate() {
        resolved = List.of(PUBLIC, address("10.0.0.1"));

        assertThat(find(ID)).isNull();
        assertThat(connectedTo).isEmpty();
    }

    @Test
    void rejectsRedirectResponse() {
        response = new Response(302, "application/json", document(ID, "").getBytes(StandardCharsets.UTF_8));

        assertThat(find(ID)).isNull();
    }

    @Test
    void rejectsDocumentOverFiveKilobytes() {
        String padded = document(ID, ", \"logo_uri\": \"https://app.example/" + "a".repeat(5 * 1024) + "\"");
        response = json(padded);

        assertThat(find(ID)).isNull();
    }

    @Test
    void rejectsNonJsonResponses() {
        for (String contentType : new String[] {"text/html", "application/jsonp", null}) {
            response = new Response(200, contentType, document(ID, "").getBytes(StandardCharsets.UTF_8));
            assertThat(find(ID)).as(String.valueOf(contentType)).isNull();
        }
        for (String body : List.of("not json", "[]", "\"https://app.example\"")) {
            response = json(body);
            assertThat(find(ID)).as(body).isNull();
        }
    }

    @Test
    void rejectsClientIdMismatch() {
        response = json(document("https://other.example/oauth/client.json", ""));

        assertThat(find(ID)).isNull();
    }

    @Test
    void rejectsMissingOrMalformedRedirectUris() {
        for (String body : List.of("{\"client_id\": \"" + ID + "\"}",
                "{\"client_id\": \"" + ID + "\", \"redirect_uris\": []}",
                "{\"client_id\": \"" + ID + "\", \"redirect_uris\": \"http://localhost/callback\"}",
                "{\"client_id\": \"" + ID + "\", \"redirect_uris\": [1]}",
                "{\"client_id\": \"" + ID + "\", \"redirect_uris\": [\"http://localhost/callback#frag\"]}")) {
            response = json(body);
            assertThat(find(ID)).as(body).isNull();
        }
    }

    @Test
    void rejectsSecretFieldsAndConfidentialAuthMethods() {
        for (String extra : List.of(", \"client_secret\": \"s\"", ", \"client_secret_expires_at\": 0",
                ", \"token_endpoint_auth_method\": \"client_secret_basic\"",
                ", \"token_endpoint_auth_method\": \"private_key_jwt\"")) {
            response = json(document(ID, extra));
            assertThat(find(ID)).as(extra).isNull();
        }
        response = json(document(ID, ", \"token_endpoint_auth_method\": \"none\""));
        assertThat(find(ID)).isNotNull();
    }

    @Test
    void rejectsWhenFetchFails() {
        failure = new IOException("연결 시간 초과");

        assertThat(find(ID)).isNull();
    }

    @Test
    void cachesDocumentForAtMost24Hours() {
        ClientMetadataDocuments documents = documents(false);

        assertThat(documents.find(ID)).isNotNull();
        clock.advance(Duration.ofHours(24).minusSeconds(1));
        assertThat(documents.find(ID)).isNotNull();
        assertThat(connectedTo).hasSize(1);

        clock.advance(Duration.ofSeconds(1));
        assertThat(documents.find(ID)).isNotNull();
        assertThat(connectedTo).hasSize(2);
    }

    /** 만료된 캐시는 다시 가져오기에 실패해도 대신 쓰지 않는다. 문서가 바뀌거나 사라졌을 수 있다. */
    @Test
    void failedRefetchDoesNotFallBackToExpiredCache() {
        ClientMetadataDocuments documents = documents(false);
        assertThat(documents.find(ID)).isNotNull();

        clock.advance(Duration.ofHours(25));
        failure = new IOException("연결 거부");
        assertThat(documents.find(ID)).isNull();
        assertThat(documents.find(ID)).isNull();
        // 두 번째는 실패 기억에 걸려 가져오지 않는다
        assertThat(connectedTo).hasSize(2);
    }

    /** 실패한 주소는 5분 동안 다시 가져오지 않고 거절한다. 그 뒤에는 다시 가져와 본다. */
    @Test
    void failedAddressIsNotFetchedAgainForFiveMinutes() {
        ClientMetadataDocuments documents = documents(false);
        failure = new IOException("연결 거부");

        assertThat(documents.find(ID)).isNull();
        clock.advance(Duration.ofMinutes(5).minusSeconds(1));
        assertThat(documents.find(ID)).isNull();
        assertThat(connectedTo).hasSize(1);

        failure = null;
        clock.advance(Duration.ofSeconds(1));
        assertThat(documents.find(ID)).isNotNull();
        assertThat(connectedTo).hasSize(2);
    }

    /** 이름 해석이 느려도 전체 시간 안에 요청 스레드를 돌려준다. 이름 해석은 멈출 수 없어 작업 스레드에서 한다. */
    @Test
    void slowNameResolutionCountsTowardTheBudget() {
        ClientMetadataDocuments documents = new ClientMetadataDocuments(host -> {
            try {
                Thread.sleep(2_000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return resolved;
        }, (uri, address, timeout, maxBytes) -> response, false, clock, Duration.ofMillis(300));
        opened.add(documents);
        long started = System.nanoTime();

        assertThat(documents.find(ID)).isNull();
        assertThat(Duration.ofNanos(System.nanoTime() - started)).isLessThan(Duration.ofSeconds(1));
    }

    /** 동시에 가져오는 문서는 4개까지다. 자리가 없으면 기다리지 않고 거절하고, 가져오기를 시작하지도 않는다. */
    @Test
    void concurrentFetchesAreCappedAtFour() throws Exception {
        CountDownLatch started = new CountDownLatch(4);
        CountDownLatch release = new CountDownLatch(1);
        ClientMetadataDocuments documents = documents(false, (uri, address, timeout, maxBytes) -> {
            connectedTo.add(address);
            started.countDown();
            try {
                release.await();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return json(document(uri.toString(), ""));
        });
        List<Thread> waiting = new ArrayList<>();
        for (int i = 0; i < 4; i++) {
            String id = "https://app" + i + ".example/client.json";
            waiting.add(Thread.ofVirtual().start(() -> documents.find(id)));
        }
        assertThat(started.await(2, TimeUnit.SECONDS)).isTrue();

        long before = System.nanoTime();
        assertThat(documents.find("https://app4.example/client.json")).isNull();
        assertThat(Duration.ofNanos(System.nanoTime() - before)).isLessThan(Duration.ofMillis(500));
        assertThat(connectedTo).hasSize(4);

        release.countDown();
        for (Thread thread : waiting) {
            thread.join();
        }
        // 자리가 비면 다시 가져온다. 거절은 실패로 기억하지 않는다(주소 탓이 아니다)
        assertThat(documents.find("https://app4.example/client.json")).isNotNull();
    }

    @Test
    void clientIdLongerThan2048IsRejectedWithoutFetching() {
        String longest = "https://app.example/" + "a".repeat(2048 - "https://app.example/".length());
        response = json(document(longest, ""));
        assertThat(find(longest)).isNotNull();

        String tooLong = longest + "a";
        response = json(document(tooLong, ""));
        assertThat(find(tooLong)).isNull();
        assertThat(connectedTo).hasSize(1);
    }

    /** client_id의 %0a가 디코딩돼 들어와도 로그에 가짜 줄을 만들지 못한다(보안 검토에서 재현한 로그 위조). */
    @Test
    void logValuesEscapeControlCharacters() {
        assertThat(ClientMetadataDocuments.loggable("https://a.example/x\n2026-09-28 WARN 가짜\r\t\u2028끝"))
                .isEqualTo("https://a.example/x\\u000a2026-09-28 WARN 가짜\\u000d\\u0009\\u2028끝")
                .doesNotContain("\n", "\r");
    }

    @Test
    void ignoresNonMetadataClientIds() {
        assertThat(find("wiki-rag-dev")).isNull();
        assertThat(find(null)).isNull();
        assertThat(connectedTo).isEmpty();
    }

    private static InetAddress address(String literal) {
        try {
            return InetAddress.getByName(literal);
        } catch (UnknownHostException e) {
            throw new IllegalArgumentException(literal, e);
        }
    }

    private static final class MutableClock extends Clock {

        private Instant now;

        MutableClock(Instant now) {
            this.now = now;
        }

        void advance(Duration duration) {
            now = now.plus(duration);
        }

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            return this;
        }

        @Override
        public Instant instant() {
            return now;
        }
    }
}
