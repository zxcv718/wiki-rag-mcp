package io.github.zxcv718.wiki.outbox;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class EventStreamsTest {

    /** README "이벤트"의 값. Python 쪽(zlib.crc32) 테스트에도 같은 값을 둔다. 어긋나면 같은 문서의 이벤트가 두 소비자로 갈린다. */
    @ParameterizedTest
    @CsvSource({"co-001, 1", "eng-022, 1", "infra-011, 3", "hr-007, 0", "fin-011, 0", "data-003, 1"})
    void partitionMatchesPythonCrc32(String docId, int partition) {
        assertThat(EventStreams.partitionOf(docId, 4)).isEqualTo(partition);
        assertThat(EventStreams.documentStream(docId, 4)).isEqualTo("wiki:events:" + partition);
    }
}
