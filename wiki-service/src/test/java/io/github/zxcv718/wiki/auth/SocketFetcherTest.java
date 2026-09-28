package io.github.zxcv718.wiki.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import io.github.zxcv718.wiki.auth.ClientMetadataDocuments.Response;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.function.Consumer;
import org.junit.jupiter.api.Test;

/**
 * 실제 소켓으로 로컬 서버에 붙어 본다. TLS는 빼고 http(로컬 점검용 경로)로 확인한다. 연결할 주소를 정하는 부분, 리다이렉트를
 * 따라가지 않는 것, 시간과 크기 한도는 TLS 여부와 상관없이 같다.
 */
class SocketFetcherTest {

    private static final InetAddress LOOPBACK = InetAddress.getLoopbackAddress();

    private final SocketFetcher fetcher = new SocketFetcher();

    /** 요청을 한 번 받아 answer로 답하는 서버를 띄우고, 이름을 해석하지 않고 그 주소로 붙는다. */
    private Response fetchFrom(Consumer<OutputStream> answer, Duration timeout) throws Exception {
        try (ServerSocket server = new ServerSocket(0, 1, LOOPBACK)) {
            Thread thread = Thread.ofVirtual().start(() -> {
                try (Socket socket = server.accept()) {
                    readRequestHead(socket.getInputStream());
                    answer.accept(socket.getOutputStream());
                } catch (IOException e) {
                    // 클라이언트가 먼저 끊으면 끝낸다
                }
            });
            try {
                // 호스트 이름은 해석되지 않는 이름이다. 주어진 주소로만 붙는지 확인한다
                URI uri = URI.create("http://cimd.invalid:" + server.getLocalPort() + "/client.json");
                return fetcher.fetch(uri, LOOPBACK, timeout, 5 * 1024);
            } finally {
                thread.interrupt();
            }
        }
    }

    private static void readRequestHead(InputStream in) throws IOException {
        int matched = 0;
        byte[] end = "\r\n\r\n".getBytes(StandardCharsets.US_ASCII);
        while (matched < end.length) {
            int b = in.read();
            if (b == -1) {
                return;
            }
            matched = b == end[matched] ? matched + 1 : (b == end[0] ? 1 : 0);
        }
    }

    private static Consumer<OutputStream> respond(String raw) {
        return out -> {
            try {
                out.write(raw.getBytes(StandardCharsets.UTF_8));
                out.flush();
            } catch (IOException e) {
                throw new IllegalStateException(e);
            }
        };
    }

    @Test
    void readsBodyWithContentLength() throws Exception {
        Response response = fetchFrom(respond("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + "Content-Length: 2\r\nConnection: close\r\n\r\n{}"), Duration.ofSeconds(3));

        assertThat(response.status()).isEqualTo(200);
        assertThat(response.contentType()).isEqualTo("application/json");
        assertThat(response.body()).asString().isEqualTo("{}");
    }

    @Test
    void doesNotFollowRedirects() throws Exception {
        Response response = fetchFrom(respond("HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/\r\n"
                + "Content-Length: 0\r\n\r\n"), Duration.ofSeconds(3));

        assertThat(response.status()).isEqualTo(302);
    }

    /** 바이트를 조금씩 흘려 읽기마다의 시간 제한을 피하는 서버도 전체 시간 안에 끊는다. */
    @Test
    void stopsAtTheDeadlineEvenIfServerTrickles() {
        Consumer<OutputStream> trickle = out -> {
            try {
                while (true) {
                    out.write('H');
                    out.flush();
                    Thread.sleep(100);
                }
            } catch (IOException | InterruptedException e) {
                // 끊기면 끝낸다
            }
        };
        long started = System.nanoTime();

        assertThatThrownBy(() -> fetchFrom(trickle, Duration.ofMillis(500))).isInstanceOf(SocketTimeoutException.class);
        assertThat(Duration.ofNanos(System.nanoTime() - started)).isLessThan(Duration.ofSeconds(2));
    }

    /**
     * TLS 핸드셰이크 중에 바이트를 흘리는 서버도 전체 시간 안에 끊는다. 핸드셰이크의 읽기는 JSSE 안에서 여러 번 일어나 읽기마다의
     * 시간 제한으로는 끊지 못한다(보안 검토의 재현: 레코드 머리로 16KB를 예고하고 1초에 1바이트씩 보내 13초 넘게 붙잡음).
     */
    @Test
    void stopsTlsHandshakeDripAtTheDeadline() throws Exception {
        try (ServerSocket server = new ServerSocket(0, 1, LOOPBACK)) {
            Thread thread = Thread.ofVirtual().start(() -> {
                try (Socket socket = server.accept()) {
                    socket.getInputStream().read(new byte[4096]);  // ClientHello
                    OutputStream out = socket.getOutputStream();
                    out.write(new byte[] {0x16, 0x03, 0x03, 0x40, 0x00});
                    while (true) {
                        out.write(0);
                        out.flush();
                        Thread.sleep(100);
                    }
                } catch (IOException | InterruptedException e) {
                    // 클라이언트가 끊으면 끝낸다
                }
            });
            long started = System.nanoTime();
            try {
                URI uri = URI.create("https://cimd.example:" + server.getLocalPort() + "/client.json");
                assertThatThrownBy(() -> fetcher.fetch(uri, LOOPBACK, Duration.ofMillis(700), 5 * 1024))
                        .isInstanceOf(SocketTimeoutException.class);
                assertThat(Duration.ofNanos(System.nanoTime() - started)).isLessThan(Duration.ofMillis(1500));
            } finally {
                thread.interrupt();
            }
        }
    }

    @Test
    void stopsReadingOversizedResponse() {
        String huge = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + "a".repeat(64 * 1024);

        assertThatThrownBy(() -> fetchFrom(respond(huge), Duration.ofSeconds(3))).isInstanceOf(IOException.class)
                .hasMessageContaining("너무 큽니다");
    }

    @Test
    void decodesChunkedBody() throws IOException {
        Response response = SocketFetcher.parse(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + "Transfer-Encoding: chunked\r\n\r\n3\r\n{\"a\r\n4;ext=1\r\n\": 1\r\n1\r\n}\r\n0\r\n\r\n")
                .getBytes(StandardCharsets.US_ASCII));

        assertThat(response.body()).asString().isEqualTo("{\"a\": 1}");
    }

    @Test
    void rejectsMalformedResponses() {
        for (String raw : new String[] {
                // 크기가 int 끝에 가까우면 start + size가 넘쳐 음수가 되던 경우
                "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n7fffffff\r\n{}\r\n0\r\n\r\n",
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n",
                "SSH-2.0-OpenSSH\r\n\r\n", "HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n{}",
                "HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\n{}",
                "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n{}\r\n0\r\n\r\n"}) {
            assertThatThrownBy(() -> SocketFetcher.parse(raw.getBytes(StandardCharsets.US_ASCII))).as(raw)
                    .isInstanceOf(IOException.class);
        }
    }
}
