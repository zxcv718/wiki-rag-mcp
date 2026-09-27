# OpenSearch 기본 이미지에는 한국어 형태소 분석기(nori)가 없어 플러그인을 설치한다 (ADR-02).
ARG OPENSEARCH_VERSION=3.8.0
FROM opensearchproject/opensearch:${OPENSEARCH_VERSION}
RUN /usr/share/opensearch/bin/opensearch-plugin install --batch analysis-nori
