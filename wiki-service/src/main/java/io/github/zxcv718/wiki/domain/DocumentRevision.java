package io.github.zxcv718.wiki.domain;

/** 야간 정합성 배치가 인덱스의 doc_state와 비교하는 값. 본문을 읽지 않게 필요한 열만 뽑는다. */
public record DocumentRevision(String docId, long revision, boolean deleted) {
}
