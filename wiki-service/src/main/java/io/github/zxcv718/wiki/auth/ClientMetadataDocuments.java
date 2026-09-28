package io.github.zxcv718.wiki.auth;

import java.io.IOException;
import java.net.InetAddress;
import java.net.URI;
import java.net.URISyntaxException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.InvalidMediaTypeException;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.server.authorization.client.RegisteredClient;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * CIMD(Client ID Metadata Document) 클라이언트 (README "클라이언트", ADR-24). client_id가 https 주소면 그 주소의
 * 메타데이터 문서를 가져와 클라이언트로 쓴다.
 *
 * client_id는 누구나 정할 수 있어, 여기는 공격자가 고른 주소로 서버가 요청을 보내는 곳이다. 그래서 README의 조건을
 * 하나라도 어기면 클라이언트로 받지 않는다. 받지 않은 이유는 로그에만 남기고, 인가 서버에는 "모르는 클라이언트"로
 * 답한다.
 *
 * 가져오기는 로그인 없이 누구나 일으킬 수 있어, 느린 서버로 위키의 요청 스레드를 붙잡는 공격을 막는다(보안 검토에서
 * 재현). 이름 해석부터 응답까지 전부 작업 스레드에서 하고, 요청 스레드는 남은 시간만 기다린다. 이름 해석은 중간에
 * 멈출 수 없어서다. 작업 스레드는 동시에 MAX_CONCURRENT개까지만 돌고, 자리가 없으면 기다리지 않고 거절한다. 실패한
 * 주소는 FAILURE_TTL 동안 기억해 다시 가져오지 않는다. 한 요청에서 인증 공급자 여러 개가 같은 클라이언트를 찾아도,
 * 성공은 캐시가, 실패는 이 기억이 받아 문서를 두 번 가져오지 않는다.
 *
 * 이름 해석과 HTTP 가져오기는 테스트에서 바꿔 끼울 수 있게 인터페이스로 뺐다. 주소 검사, 응답 검사, 문서 검사, 캐시는
 * 모두 이 클래스가 한다.
 */
public class ClientMetadataDocuments implements AutoCloseable {

    static final Duration TIMEOUT = Duration.ofSeconds(3);
    static final int MAX_BYTES = 5 * 1024;
    static final int MAX_CLIENT_ID_LENGTH = 2048;
    static final int MAX_CONCURRENT = 4;
    static final Duration CACHE_TTL = Duration.ofHours(24);
    static final Duration FAILURE_TTL = Duration.ofMinutes(5);

    /** client_id는 공격자가 정하므로 캐시와 실패 기억이 끝없이 커지지 않게 막는다. 넘치면 가장 오래 안 쓴 것부터 버린다. */
    private static final int CACHE_SIZE = 1000;

    private static final Logger log = LoggerFactory.getLogger(ClientMetadataDocuments.class);

    /** 호스트 이름을 IP 주소로 바꾼다. */
    public interface AddressResolver {
        List<InetAddress> resolve(String host) throws IOException;
    }

    /**
     * uri의 문서를 GET으로 가져온다. 연결은 address로만 하고(TLS 인증서는 uri의 호스트로 확인), 리다이렉트는 따라가지
     * 않으며, timeout이 지나면 TLS 핸드셰이크 중이라도 연결을 끊고 IOException을 던진다. 본문은 maxBytes를 넘으면 더
     * 읽지 않아도 된다.
     */
    public interface Fetcher {
        Response fetch(URI uri, InetAddress address, Duration timeout, int maxBytes) throws IOException;
    }

    public record Response(int status, String contentType, byte[] body) {
    }

    private record Cached(RegisteredClient client, Instant fetchedAt) {
    }

    private static final class Rejected extends Exception {
        Rejected(String message) {
            super(message);
        }
    }

    private final AddressResolver resolver;
    private final Fetcher fetcher;
    private final boolean allowHttpLocalhost;
    private final Clock clock;
    private final Duration timeout;
    private final Semaphore permits = new Semaphore(MAX_CONCURRENT);
    private final ExecutorService workers = Executors.newFixedThreadPool(MAX_CONCURRENT,
            Thread.ofPlatform().name("cimd-fetch-", 0).daemon().factory());
    private final Map<String, Cached> cache = boundedMap();
    private final Map<String, Instant> failures = boundedMap();

    public ClientMetadataDocuments(AddressResolver resolver, Fetcher fetcher, boolean allowHttpLocalhost, Clock clock) {
        this(resolver, fetcher, allowHttpLocalhost, clock, TIMEOUT);
    }

    /** 시간 한도를 줄여 테스트를 빨리 돌리려고 둔 생성자. */
    ClientMetadataDocuments(AddressResolver resolver, Fetcher fetcher, boolean allowHttpLocalhost, Clock clock,
                            Duration timeout) {
        this.resolver = resolver;
        this.fetcher = fetcher;
        this.allowHttpLocalhost = allowHttpLocalhost;
        this.clock = clock;
        this.timeout = timeout;
    }

    private static <V> Map<String, V> boundedMap() {
        return new LinkedHashMap<>(16, 0.75f, true) {
            @Override
            protected boolean removeEldestEntry(Map.Entry<String, V> eldest) {
                return size() > CACHE_SIZE;
            }
        };
    }

    /** CIMD 형식의 client_id가 아니거나 조건을 어기면 null. */
    RegisteredClient find(String clientId) {
        if (!isMetadataUrl(clientId)) {
            return null;
        }
        if (clientId.length() > MAX_CLIENT_ID_LENGTH) {
            log.info("CIMD 클라이언트를 받지 않았습니다: client_id가 {}자로 {}자를 넘습니다", clientId.length(),
                    MAX_CLIENT_ID_LENGTH);
            return null;
        }
        long deadline = System.nanoTime() + timeout.toNanos();
        Instant now = clock.instant();
        synchronized (cache) {
            Cached cached = cache.get(clientId);
            if (cached != null && now.isBefore(cached.fetchedAt().plus(CACHE_TTL))) {
                return cached.client();
            }
            // 만료된 문서는 가져오기에 실패해도 대신 쓰지 않는다
            cache.remove(clientId);
            Instant failedAt = failures.get(clientId);
            if (failedAt != null && now.isBefore(failedAt.plus(FAILURE_TTL))) {
                return null;
            }
        }
        if (!permits.tryAcquire()) {
            log.info("CIMD 클라이언트를 받지 않았습니다: {} (동시에 가져오는 문서가 {}개로 가득 찼습니다)",
                    loggable(clientId), MAX_CONCURRENT);
            return null;
        }
        Future<RegisteredClient> loading;
        try {
            loading = workers.submit(() -> {
                try {
                    return load(clientId, deadline);
                } finally {
                    permits.release();
                }
            });
        } catch (RejectedExecutionException e) {
            permits.release();
            return null;
        }
        RegisteredClient client;
        try {
            // 시간이 지나면 기다리기만 그만둔다. 작업 스레드는 Fetcher가 연결을 끊어 곧 끝나고, 그때 자리를 돌려준다
            client = loading.get(Math.max(0, deadline - System.nanoTime()), TimeUnit.NANOSECONDS);
        } catch (TimeoutException e) {
            return rejected(clientId, now, "시간 안에 가져오지 못했습니다");
        } catch (ExecutionException e) {
            return rejected(clientId, now, String.valueOf(e.getCause().getMessage()));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return null;
        }
        synchronized (cache) {
            failures.remove(clientId);
            cache.put(clientId, new Cached(client, now));
        }
        return client;
    }

    private RegisteredClient rejected(String clientId, Instant now, String reason) {
        synchronized (cache) {
            failures.put(clientId, now);
        }
        log.info("CIMD 클라이언트를 받지 않았습니다: {} ({})", loggable(clientId), loggable(reason));
        return null;
    }

    /**
     * 로그에 남길 값. client_id와 오류 이유에는 공격자가 정한 글자가 들어가므로, 줄바꿈 같은 제어 문자를 \\uXXXX로 바꿔
     * 가짜 로그 줄을 끼워 넣지 못하게 한다.
     */
    static String loggable(String value) {
        StringBuilder out = new StringBuilder(value.length());
        for (char c : value.toCharArray()) {
            if (Character.isISOControl(c) || c == '\u2028' || c == '\u2029') {
                out.append(String.format("\\u%04x", (int) c));
            } else {
                out.append(c);
            }
        }
        return out.toString();
    }

    @Override
    public void close() {
        workers.shutdownNow();
    }

    private boolean isMetadataUrl(String clientId) {
        return clientId != null && (clientId.startsWith("https://")
                || (allowHttpLocalhost && clientId.startsWith("http://localhost")));
    }

    private RegisteredClient load(String clientId, long deadline) throws Rejected, IOException {
        URI uri = checkedUri(clientId);
        boolean local = "http".equals(uri.getScheme());
        List<InetAddress> addresses = resolver.resolve(uri.getHost());
        if (addresses.isEmpty()) {
            throw new Rejected("주소를 해석하지 못했습니다");
        }
        for (InetAddress address : addresses) {
            // http는 로컬 점검용이라 루프백만, https는 공인 주소만 받는다
            if (local ? !address.isLoopbackAddress() : !PublicAddress.isPublic(address)) {
                throw new Rejected("공인 주소가 아닙니다: " + address.getHostAddress());
            }
        }
        Duration remaining = Duration.ofNanos(deadline - System.nanoTime());
        if (remaining.isNegative() || remaining.isZero()) {
            throw new Rejected("시간 안에 가져오지 못했습니다");
        }
        // 검사한 주소로만 붙는다. 다시 해석하면 검사 뒤에 DNS 답이 내부 주소로 바뀔 수 있다(DNS rebinding)
        Response response = fetcher.fetch(uri, addresses.getFirst(), remaining, MAX_BYTES);
        return toClient(clientId, response);
    }

    /** 프래그먼트, 사용자 정보, '.'과 '..' 경로 세그먼트가 없는 절대 주소. http는 localhost만 받는다. */
    private static URI checkedUri(String clientId) throws Rejected {
        URI uri;
        try {
            uri = new URI(clientId);
        } catch (URISyntaxException e) {
            throw new Rejected("주소 형식이 틀렸습니다");
        }
        if (uri.getHost() == null || uri.getRawFragment() != null || uri.getRawUserInfo() != null) {
            throw new Rejected("호스트가 없거나 프래그먼트, 사용자 정보가 있습니다");
        }
        if ("http".equals(uri.getScheme()) && !"localhost".equalsIgnoreCase(uri.getHost())) {
            throw new Rejected("http는 localhost만 받습니다");
        }
        // 디코딩한 경로로 본다. %2e%2e도 '..'으로 걸린다
        for (String segment : uri.getPath().split("/", -1)) {
            if (segment.equals(".") || segment.equals("..")) {
                throw new Rejected("경로에 '.'이나 '..' 세그먼트가 있습니다");
            }
        }
        return uri;
    }

    private static RegisteredClient toClient(String clientId, Response response) throws Rejected {
        if (response.status() != 200) {
            // 3xx도 여기서 거절한다. 리다이렉트를 따라가면 검사하지 않은 주소로 가게 된다
            throw new Rejected("응답 상태가 200이 아닙니다: " + response.status());
        }
        if (!isJson(response.contentType())) {
            throw new Rejected("application/json 응답이 아닙니다: " + response.contentType());
        }
        if (response.body().length > MAX_BYTES) {
            throw new Rejected("문서가 " + MAX_BYTES + "바이트보다 큽니다");
        }
        JsonNode document = parse(response.body());
        if (!clientId.equals(document.path("client_id").stringValue(null))) {
            throw new Rejected("문서의 client_id가 가져온 주소와 다릅니다");
        }
        for (String name : document.propertyNames()) {
            if (name.startsWith("client_secret")) {
                throw new Rejected("공개 클라이언트 문서에 " + name + "가 있습니다");
            }
        }
        JsonNode method = document.get("token_endpoint_auth_method");
        if (method != null && !"none".equals(method.stringValue(null))) {
            throw new Rejected("token_endpoint_auth_method가 none이 아닙니다");
        }
        JsonNode redirects = document.path("redirect_uris");
        if (!redirects.isArray()) {
            throw new Rejected("redirect_uris가 없거나 배열이 아닙니다");
        }
        List<String> redirectUris = new ArrayList<>();
        for (JsonNode uri : redirects) {
            if (!uri.isString()) {
                throw new Rejected("redirect_uris에 문자열이 아닌 값이 있습니다");
            }
            redirectUris.add(uri.stringValue());
        }
        if (redirectUris.isEmpty()) {
            throw new Rejected("redirect_uris가 없습니다");
        }
        // 이름은 문서가 주장하는 값이라 화면 표시에만 쓴다. 동의 화면은 client_id의 호스트를 앞세운다
        String name = document.path("client_name").stringValue(clientId);
        try {
            return ClientRegistry.publicClient(clientId, name, redirectUris);
        } catch (IllegalArgumentException e) {
            throw new Rejected("redirect_uris에 쓸 수 없는 주소가 있습니다");
        }
    }

    private static boolean isJson(String contentType) {
        try {
            return contentType != null && MediaType.APPLICATION_JSON.equalsTypeAndSubtype(MediaType.parseMediaType(contentType));
        } catch (InvalidMediaTypeException e) {
            return false;
        }
    }

    private static JsonNode parse(byte[] body) throws Rejected {
        JsonNode document;
        try {
            document = JsonMapper.shared().readTree(body);
        } catch (JacksonException e) {
            throw new Rejected("JSON이 아닙니다");
        }
        if (document == null || !document.isObject()) {
            throw new Rejected("JSON 객체가 아닙니다");
        }
        return document;
    }
}
