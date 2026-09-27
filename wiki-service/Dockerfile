# syntax=docker/dockerfile:1
# 빌드 단계: JDK와 Gradle wrapper로 실행 jar를 만든다. 테스트는 Testcontainers가 Docker를 써야 해서 이미지
# 빌드 안에서는 돌리지 않고, 빌드 전에 ./gradlew test로 따로 돌린다.
FROM eclipse-temurin:21-jdk AS build
WORKDIR /workspace
COPY gradlew settings.gradle build.gradle ./
COPY gradle gradle
COPY src src
# 캐시 마운트로 Gradle 배포판과 의존성을 빌드 사이에 재사용한다. 이미지 층에는 남지 않는다
RUN --mount=type=cache,target=/root/.gradle ./gradlew --no-daemon bootJar

# 실행 단계: JRE만 둔다. 컴파일러와 빌드 도구가 없는 작은 이미지로, 루트가 아닌 사용자로 돌린다
FROM eclipse-temurin:21-jre
RUN groupadd --system wiki && useradd --system --gid wiki --no-create-home wiki
WORKDIR /app
COPY --from=build /workspace/build/libs/wiki-service.jar app.jar
USER wiki
EXPOSE 8081
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
