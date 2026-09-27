package io.github.zxcv718.wiki.outbox;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 폴러와 정리 작업의 실행 주기. 테스트는 wiki.outbox.relay.enabled=false로 이 스케줄을 끄고 OutboxRelay를 직접
 * 불러, 언제 발행되는지를 테스트가 정하게 한다.
 */
@Component
@ConditionalOnProperty(name = "wiki.outbox.relay.enabled", havingValue = "true", matchIfMissing = true)
class OutboxSchedule {

    private final OutboxRelay relay;

    OutboxSchedule(OutboxRelay relay) {
        this.relay = relay;
    }

    // 앞 주기가 끝난 뒤 0.5초를 쉰다. 발행이 밀려도 주기가 겹쳐 쌓이지 않는다
    @Scheduled(fixedDelay = 500)
    void relay() {
        relay.publishPending();
    }

    // 컨테이너 시간대(UTC)와 관계없이 한국 시간 새벽 4시에 돈다
    @Scheduled(cron = "0 0 4 * * *", zone = "Asia/Seoul")
    void deleteExpired() {
        relay.deleteExpired();
    }
}
