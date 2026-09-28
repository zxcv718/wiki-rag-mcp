package io.github.zxcv718.wiki.auth;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

/**
 * CIMD 문서를 가져오는 HTTP 클라이언트. 이름을 다시 해석하지 않고 검사를 마친 IP로 직접 소켓을 연다.
 *
 * JDK HttpClient는 연결할 IP를 정할 수 없어 쓰지 않았다. 호스트 이름을 주면 연결할 때 다시 해석하므로, 검사한 주소와 실제로
 * 붙는 주소가 달라질 수 있다(DNS rebinding). TLS는 SNI와 인증서 확인 모두 원래 호스트 이름으로 한다. 요청은 GET 하나이고,
 * 리다이렉트는 따라가지 않으며(3xx도 응답으로 돌려준다), 연결부터 마지막 바이트까지 timeout 안에 끝나야 한다.
 *
 * 읽기마다 거는 시간 제한(SO_TIMEOUT)만으로는 바이트를 조금씩 흘리는 서버를 끊지 못한다. 특히 TLS 핸드셰이크 중에는
 * 읽기가 JSSE 안에서 여러 번 일어나 전체 시간을 셀 곳이 없다(보안 검토에서 1초에 1바이트씩 흘려 13초 넘게 붙잡힘).
 * 그래서 감시 스레드가 마감 시각에 소켓을 닫는다.
 */
final class SocketFetcher implements ClientMetadataDocuments.Fetcher {

    /** 헤더에 쓸 여유. 본문 한도와 합친 크기까지만 읽는다. */
    private static final int HEADER_ALLOWANCE = 16 * 1024;

    /** 마감 시각에 소켓을 닫는 감시 스레드. 할 일은 소켓 닫기뿐이라 하나로 충분하다. */
    private static final ScheduledExecutorService WATCHDOG = Executors.newSingleThreadScheduledExecutor(
            Thread.ofPlatform().name("cimd-fetch-watchdog").daemon().factory());

    @Override
    public ClientMetadataDocuments.Response fetch(URI uri, InetAddress address, Duration timeout, int maxBytes)
            throws IOException {
        long deadline = System.nanoTime() + timeout.toNanos();
        boolean tls = "https".equals(uri.getScheme());
        int port = uri.getPort() != -1 ? uri.getPort() : (tls ? 443 : 80);
        Socket raw = new Socket();
        ScheduledFuture<?> watchdog = WATCHDOG.schedule(() -> closeQuietly(raw), timeout.toNanos(), TimeUnit.NANOSECONDS);
        try (raw) {
            raw.connect(new InetSocketAddress(address, port), remainingMillis(deadline));
            raw.setSoTimeout(remainingMillis(deadline));
            Socket socket = raw;
            if (tls) {
                SSLSocket ssl = (SSLSocket) ((SSLSocketFactory) SSLSocketFactory.getDefault())
                        .createSocket(raw, uri.getHost(), port, true);
                SSLParameters parameters = ssl.getSSLParameters();
                parameters.setEndpointIdentificationAlgorithm("HTTPS");
                ssl.setSSLParameters(parameters);
                ssl.startHandshake();
                socket = ssl;
            }
            String target = (uri.getRawPath().isEmpty() ? "/" : uri.getRawPath())
                    + (uri.getRawQuery() == null ? "" : "?" + uri.getRawQuery());
            String host = uri.getPort() == -1 ? uri.getHost() : uri.getHost() + ":" + uri.getPort();
            socket.getOutputStream().write(("GET " + target + " HTTP/1.1\r\nHost: " + host
                    + "\r\nAccept: application/json\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
            socket.getOutputStream().flush();
            return parse(readAll(socket, deadline, HEADER_ALLOWANCE + maxBytes));
        } catch (IOException e) {
            // 감시 스레드가 닫았으면 "소켓이 닫힘"보다 원래 이유(시간 초과)로 알린다
            if (System.nanoTime() - deadline >= 0) {
                throw new SocketTimeoutException("시간 안에 가져오지 못했습니다");
            }
            throw e;
        } finally {
            watchdog.cancel(false);
        }
    }

    private static void closeQuietly(Socket socket) {
        try {
            socket.close();
        } catch (IOException e) {
            // 닫는 중의 오류는 가져오기 쪽에서 IOException으로 드러난다
        }
    }

    /** 서버가 연결을 닫을 때까지 읽는다(Connection: close). 한도를 넘거나 시간이 지나면 IOException. */
    private static byte[] readAll(Socket socket, long deadline, int limit) throws IOException {
        InputStream in = socket.getInputStream();
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        while (true) {
            socket.setSoTimeout(remainingMillis(deadline));
            int read = in.read(buffer);
            if (read == -1) {
                return out.toByteArray();
            }
            out.write(buffer, 0, read);
            if (out.size() > limit) {
                throw new IOException("응답이 너무 큽니다");
            }
        }
    }

    private static int remainingMillis(long deadline) throws SocketTimeoutException {
        long remaining = Duration.ofNanos(deadline - System.nanoTime()).toMillis();
        if (remaining <= 0) {
            throw new SocketTimeoutException("시간 안에 가져오지 못했습니다");
        }
        return (int) remaining;
    }

    /** HTTP/1.1 응답 전체를 상태, Content-Type, 본문으로 나눈다. 본문은 Content-Length나 chunked 인코딩을 따른다. */
    static ClientMetadataDocuments.Response parse(byte[] raw) throws IOException {
        int headerEnd = indexOf(raw, "\r\n\r\n".getBytes(StandardCharsets.US_ASCII), 0);
        if (headerEnd < 0) {
            throw new IOException("응답 헤더가 끝나지 않았습니다");
        }
        String[] lines = new String(raw, 0, headerEnd, StandardCharsets.ISO_8859_1).split("\r\n");
        String[] statusLine = lines[0].split(" ", 3);
        if (statusLine.length < 2 || !statusLine[0].startsWith("HTTP/1.")) {
            throw new IOException("HTTP 응답이 아닙니다");
        }
        int status;
        try {
            status = Integer.parseInt(statusLine[1]);
        } catch (NumberFormatException e) {
            throw new IOException("상태 코드를 읽지 못했습니다");
        }
        Map<String, String> headers = new HashMap<>();
        for (int i = 1; i < lines.length; i++) {
            int colon = lines[i].indexOf(':');
            if (colon > 0) {
                headers.put(lines[i].substring(0, colon).trim().toLowerCase(Locale.ROOT), lines[i].substring(colon + 1).trim());
            }
        }
        byte[] body = Arrays.copyOfRange(raw, headerEnd + 4, raw.length);
        String transferEncoding = headers.get("transfer-encoding");
        if (transferEncoding != null) {
            if (!transferEncoding.equalsIgnoreCase("chunked")) {
                throw new IOException("지원하지 않는 전송 인코딩입니다: " + transferEncoding);
            }
            body = dechunk(body);
        } else if (headers.containsKey("content-length")) {
            int length;
            try {
                length = Integer.parseInt(headers.get("content-length"));
            } catch (NumberFormatException e) {
                throw new IOException("Content-Length를 읽지 못했습니다");
            }
            if (length < 0 || length > body.length) {
                throw new IOException("본문이 Content-Length보다 짧습니다");
            }
            body = Arrays.copyOf(body, length);
        }
        return new ClientMetadataDocuments.Response(status, headers.get("content-type"), body);
    }

    private static byte[] dechunk(byte[] data) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] crlf = "\r\n".getBytes(StandardCharsets.US_ASCII);
        int position = 0;
        while (true) {
            int lineEnd = indexOf(data, crlf, position);
            if (lineEnd < 0) {
                throw new IOException("chunk 크기 줄이 끝나지 않았습니다");
            }
            String sizeLine = new String(data, position, lineEnd - position, StandardCharsets.US_ASCII);
            int semicolon = sizeLine.indexOf(';');
            int size;
            try {
                size = Integer.parseInt((semicolon < 0 ? sizeLine : sizeLine.substring(0, semicolon)).trim(), 16);
            } catch (NumberFormatException e) {
                throw new IOException("chunk 크기를 읽지 못했습니다");
            }
            if (size == 0) {
                return out.toByteArray();
            }
            int start = lineEnd + 2;
            // start + size + 2로 비교하면 큰 크기에서 int가 넘쳐 음수가 돼 검사를 통과한다
            if (size < 0 || size > data.length - start - 2) {
                throw new IOException("chunk가 잘렸습니다");
            }
            out.write(data, start, size);
            position = start + size + 2;
        }
    }

    private static int indexOf(byte[] data, byte[] pattern, int from) {
        outer:
        for (int i = from; i <= data.length - pattern.length; i++) {
            for (int j = 0; j < pattern.length; j++) {
                if (data[i + j] != pattern[j]) {
                    continue outer;
                }
            }
            return i;
        }
        return -1;
    }
}
