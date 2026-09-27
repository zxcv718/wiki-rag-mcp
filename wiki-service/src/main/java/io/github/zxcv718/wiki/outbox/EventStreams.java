package io.github.zxcv718.wiki.outbox;

import java.nio.charset.StandardCharsets;
import java.util.zip.CRC32;

/**
 * 이벤트 스트림 이름.
 *
 * 문서 이벤트는 doc_id로 스트림을 나눈다. 스트림마다 소비자를 하나만 두면 같은 문서의 이벤트를 한 번에 하나씩
 * 처리하게 된다 (ADR-19). 인덱서(Python)도 같은 함수로 파티션을 계산하므로, zlib.crc32와 같은 값이 나오는 CRC32를
 * 쓰고 양쪽 테스트에 같은 값을 둔다.
 */
public final class EventStreams {

    public static final String MEMBERSHIP = "wiki:membership";

    private EventStreams() {
    }

    public static int partitionOf(String docId, int partitions) {
        CRC32 crc = new CRC32();
        crc.update(docId.getBytes(StandardCharsets.UTF_8));
        // getValue()는 부호 없는 32비트 값을 long으로 준다. Python zlib.crc32도 부호 없는 값이라 나머지가 같다
        return (int) (crc.getValue() % partitions);
    }

    public static String documentStream(String docId, int partitions) {
        return "wiki:events:" + partitionOf(docId, partitions);
    }
}
