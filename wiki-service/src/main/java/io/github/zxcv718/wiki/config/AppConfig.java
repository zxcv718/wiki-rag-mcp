package io.github.zxcv718.wiki.config;

import java.time.Clock;
import java.time.Duration;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;

@Configuration(proxyBeanMethods = false)
@EnableScheduling
@EnableConfigurationProperties(WikiProperties.class)
public class AppConfig {

    /**
     * PostgreSQL timestamptz는 마이크로초까지만 저장한다. 시계를 마이크로초 단위로 맞춰 두면 방금 만든 응답의 시각과
     * 나중에 다시 읽은 시각이 같다.
     */
    @Bean
    Clock clock() {
        return Clock.tick(Clock.systemUTC(), Duration.ofNanos(1_000));
    }
}
