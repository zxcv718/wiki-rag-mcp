package io.github.zxcv718.wiki;

import io.github.zxcv718.wiki.auth.ClientMetadataDocuments;
import io.github.zxcv718.wiki.auth.ClientMetadataDocuments.Response;
import java.io.IOException;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;

/**
 * 통합 테스트에서 CIMD 문서를 인터넷 대신 이 맵에서 가져온다. 모든 호스트가 공인 주소 하나로 해석된다. 주소 검사와 문서
 * 검사는 실제 코드(ClientMetadataDocuments)를 그대로 탄다. 문서는 캐시되므로 테스트마다 다른 주소를 쓴다.
 */
@TestConfiguration(proxyBeanMethods = false)
class FakeClientMetadata {

    static final Map<String, Response> DOCUMENTS = new ConcurrentHashMap<>();
    /** 주소마다 가져온 횟수. 한 요청에서 문서를 두 번 가져오지 않는지 센다. */
    static final Map<String, AtomicInteger> FETCHES = new ConcurrentHashMap<>();

    static void serve(String url, String json) {
        DOCUMENTS.put(url, new Response(200, "application/json", json.getBytes(StandardCharsets.UTF_8)));
    }

    @Bean
    @Primary
    ClientMetadataDocuments fakeClientMetadataDocuments(Clock clock) throws IOException {
        InetAddress publicAddress = InetAddress.getByName("93.184.216.34");
        return new ClientMetadataDocuments(host -> List.of(publicAddress), (uri, address, timeout, maxBytes) -> {
            FETCHES.computeIfAbsent(uri.toString(), key -> new AtomicInteger()).incrementAndGet();
            Response response = DOCUMENTS.get(uri.toString());
            if (response == null) {
                throw new IOException("문서가 없습니다: " + uri);
            }
            return response;
        }, false, clock);
    }
}
