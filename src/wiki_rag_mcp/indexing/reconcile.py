"""야간 정합성 배치 (5장 "장애 대응").

위키의 문서별 revision과 인덱스의 문서 상태를 비교해, 어긋난 문서마다 그 문서의 스트림에 이벤트를 넣는다.
배치가 인덱스를 직접 고치지 않는 이유는, 직접 쓰면 스트림 소비자와 같은 문서를 동시에 쓰게 되기 때문이다.
이벤트로 넣으면 평소 경로(문서별 직렬 처리, 상태 재조회)를 그대로 탄다.

버전이 아니라 revision을 비교하므로 내용은 같고 권한·등급만 어긋난 문서도 잡힌다(ADR-19). 이벤트 경로가
완벽하다고 가정하지 않는 안전망이다. 회수 이벤트가 DLQ에서 계속 실패하거나 Redis가 발행된 이벤트를 잃어도
여기서 다시 잡힌다.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from wiki_rag_mcp.indexing.events import Event
from wiki_rag_mcp.search.backend import SearchStore
from wiki_rag_mcp.wiki.source import IndexSource


@dataclass
class ReconcileReport:
    checked: int = 0
    republished: list[Event] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)  # 이벤트로 고칠 수 없어 사람이 봐야 하는 문서


def reconcile(source: IndexSource, store: SearchStore, publish: Callable[[Event], None]) -> ReconcileReport:
    report = ReconcileReport()
    wiki = {entry.doc_id: entry for entry in source.revisions()}
    stored = store.doc_states()
    # 상태 기록 없이 청크만 있는 문서. 상태 테이블이 생기기 전에 색인된 문서가 여기에 해당한다
    without_state = store.indexed_doc_ids() - stored.keys()

    def republish(doc_id: str, revision: int) -> None:
        event = Event(doc_id, revision, "RECONCILE")
        publish(event)
        report.republished.append(event)

    for doc_id, entry in wiki.items():
        report.checked += 1
        state = stored.get(doc_id)
        if state is None:
            # 삭제된 문서가 인덱스에도 없으면 맞는 상태다
            if not entry.deleted or doc_id in without_state:
                republish(doc_id, entry.revision)
        elif state.revision < entry.revision:
            republish(doc_id, entry.revision)
        elif state.revision > entry.revision:
            # 위키를 백업에서 되돌린 경우 등. 이벤트는 revision이 낮아 건너뛰므로 전체 재색인이 필요하다
            report.anomalies.append(f"{doc_id}: 인덱스 revision {state.revision} > 위키 revision {entry.revision}")
        elif state.deleted != entry.deleted:
            report.anomalies.append(f"{doc_id}: revision {entry.revision}에서 삭제 여부가 다르다")

    # 위키에 없는 문서가 인덱스에 남아 있으면, 기록된 revision보다 큰 값으로 이벤트를 넣는다.
    # 인덱서가 위키에서 404를 받고 청크를 지운다
    for doc_id, state in stored.items():
        if doc_id not in wiki and not state.deleted:
            report.checked += 1
            republish(doc_id, state.revision + 1)
    for doc_id in without_state - wiki.keys():
        report.checked += 1
        republish(doc_id, 1)
    return report
