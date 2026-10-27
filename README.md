# wiki-rag-mcp — AWS 클라우드 배포 (B3-1)

사내 위키를 사용자 권한에 맞게 검색하는 MCP 서버를 AWS 서울 리전의 EC2에 배포한 프로젝트입니다. 코디세이 B3-1 「내가 만든 웹사이트를 인터넷에 올려 누구나 쓰게 하기」의 제출물과 검증 방법을 이 README에 모았습니다.

외부에 공개하는 웹 페이지와 `/health`는 로그인 없이 확인할 수 있습니다. 위키 검색은 OAuth 로그인이 필요합니다. 검색 서버의 동작과 실험 결과는 [사내 위키 검색 서버 설명](docs/project-overview.md)에 있습니다.

## 제출물과 증빙

| 과제 제출물 | 파일·선택 방식 | 확인할 내용 |
|---|---|---|
| 아키텍처 다이어그램 1장 | [docs/architecture.png](docs/architecture.png) | VPC, Public Subnet, Internet Gateway, EC2, Security Group과 외부 요청 흐름 |
| 외부 접속 증빙 | **방식 B**: `GET http://<퍼블릭 IP>/health` | `200 OK`, 고정 본문 `OK`. [스크린샷](docs/evidence/terminal-external-access.png), [원본 출력](docs/evidence/external-access.txt) |
| 트러블슈팅 보고서 | [docs/troubleshooting.md](docs/troubleshooting.md) | 실제 문제 4건의 증상 → 가설 → 검증 → 조치 → 결과 → 재발 방지 |
| 리소스 정리 체크리스트 | [docs/cleanup-checklist.md](docs/cleanup-checklist.md) | EC2·EBS·Elastic IP·IGW·VPC의 삭제 순서와 실행 기록 |

보관한 증빙은 운영 스택의 **2026-09-28 접속 기록**과 평가용 스택의 **2026-10-07 리허설 2회 기록**입니다. 리허설에서는 각각 Terraform 자원 13개를 삭제하고 잔여 자원 점검이 모두 0인 것을 확인했습니다. 실제 평가 1~3회의 정리 기록과 운영 스택의 삭제 기록은 아직 작성되지 않았습니다. 과거 증빙을 현재 접속 상태나 전체 자원 정리 완료로 해석하지 않습니다.

## 평가 방식: 평가마다 만들고 지움

과제는 실행 중인 웹 서버와 실습 종료 후 정리 완료를 모두 확인합니다. 평가용 스택을 새로 만들어 접속을 보여 주고, 평가 마지막에 지운 뒤 정리 기록을 남깁니다.

| 구분 | 평가용 스택 | 운영 스택 |
|---|---|---|
| Terraform workspace | `b3-1` | `default` |
| 용도 | B3-1 외부 접속·정리 시연 | 위키 검색 서버 운영과 측정 |
| EC2 / EBS | `t3.micro` / 8GiB | `m7i-flex.large` / 20GiB |
| 서버 안 | Caddy 컨테이너 1개, 소개 페이지와 `/health` | Caddy, 검색 서버, 위키 서비스, 워커, DB, Redis, 관측성 도구 |
| 주소 | 생성 때 출력하는 IP, `b3-1.dmssh.store` | 증빙 당시 `52.78.251.4`, `mcp.dmssh.store`, `auth.dmssh.store` |
| 정리 | 평가마다 생성·삭제 | 별도 정리 대상. 삭제 기록은 실행 전 |

두 스택은 [같은 Terraform 코드](infra/main.tf)를 쓰되 상태 파일을 나눕니다. 평가용 자원에는 이름의 `-b3-1` 접미사와 `stack=b3-1` 태그를 더합니다. [평가 스크립트](infra/b3-1.sh)는 자기 프로세스에만 `TF_WORKSPACE=b3-1`을 지정합니다. 리허설에서 평가용 스택 생성·삭제 전후 운영 스택의 `terraform plan`이 `No changes`인 것을 확인했습니다.

### 평가용 스택 준비

명령은 저장소 루트에서 실행합니다. AWS CLI, Terraform(버전 조건은 [infra/main.tf](infra/main.tf)), SSH, `curl`, `dig`가 필요합니다.

1. 최소 권한 IAM 사용자 `wiki-rag-deployer`의 AWS CLI 프로필 `wiki-rag`를 준비합니다. [IAM 정책](infra/iam/deployer-policy.json)을 사용하며 관리자 권한은 부여하지 않습니다.
2. SSH 키를 준비합니다. 기본 공개 키 경로는 `~/.ssh/wiki-rag-m5.pub`, 접속에 사용하는 개인 키는 `~/.ssh/wiki-rag-m5.pem`입니다. 다른 경로는 [Terraform 변수](infra/variables.tf)에 맞춥니다.
3. `infra/terraform.tfvars`가 없으면 [설정 예시](infra/terraform.tfvars.example)를 복사하고 `ssh_cidr`에 운영자 IP(`/32`)를 적습니다. 삭제 때도 이 필수 변수를 읽으므로 설정 파일을 준비합니다. 생성 시에는 스크립트가 현재 PC의 IP로 덮어씁니다.
4. DuckDNS 이름과 토큰을 저장소 밖 `~/.config/wiki-rag/duckdns.env`에 준비합니다. 변수 이름은 `DUCKDNS_SUBDOMAIN`, `DUCKDNS_TOKEN`이고, 파일 권한은 `600`으로 둡니다. 가비아의 `b3-1.dmssh.store` CNAME은 `<DUCKDNS_SUBDOMAIN>.duckdns.org`를 가리키게 합니다.
5. Terraform 공급자를 초기화하고 로그인 주체를 확인합니다.

   ```bash
   terraform -chdir=infra init
   aws sts get-caller-identity --profile wiki-rag --query Arn --output text
   # ...:user/wiki-rag-deployer 여야 한다
   ```

DuckDNS 토큰은 평가용 서버의 `user_data`와 로컬 Terraform 상태·백업에도 남습니다. 이 파일들은 커밋하지 않습니다. 컨테이너가 메타데이터를 읽지 못하도록 평가용 인스턴스의 메타데이터 홉 제한을 1로 두며, 리허설에서 호스트는 읽고 컨테이너는 시간 초과로 실패하는 것을 확인했습니다.

### 생성 → 검증 → 정리

```bash
infra/b3-1.sh up       # 계획을 확인하고 yes. 마지막에 IP·HTTPS 주소·SSH 명령 출력
```

스크립트는 실행한 PC의 공인 IP를 SSH 허용 출처로 넣습니다. 서버는 cloud-init으로 Docker 설치, DuckDNS 갱신, Caddy 실행을 진행합니다. 리허설은 `infra/b3-1.sh up --staging`으로 실행합니다. 시험 인증서는 브라우저가 신뢰하지 않으므로 정식 HTTPS 접속 증빙과 구분합니다.

출력된 IP를 아래 `<퍼블릭 IP>`에 넣고, 노트북에서 확인합니다.

```bash
curl -i http://<퍼블릭 IP>/health   # HTTP/1.1 200 OK, 본문 OK
ssh -i ~/.ssh/wiki-rag-m5.pem ubuntu@<퍼블릭 IP>
```

SSH 접속 후 서버 안에서도 확인합니다.

```bash
sudo docker ps                        # caddy:2.11.4-alpine, Up, 80·443 매핑
curl -i http://localhost               # 200
curl -i http://localhost/health        # 200, 본문 OK
curl -I https://example.com            # 인터넷 아웃바운드 연결 확인
```

검증 뒤 로컬 터미널에서 평가용 스택을 지웁니다.

```bash
infra/b3-1.sh down     # DuckDNS 값 비움 → 삭제 계획 확인·yes → 잔여 자원 점검
```

`Destroy complete! Resources: 13 destroyed.`와 잔여 자원 점검의 모든 줄이 `0`인지 확인하고, [체크리스트의 정리 실행 기록](docs/cleanup-checklist.md#정리-실행-기록)에 해당 회차의 일시와 결과를 남깁니다. 삭제 명령을 실행했다는 사실만으로 정리 완료로 보지 않습니다.

## 외부 접속 검증

**선택한 방식은 B**입니다. 보안 그룹의 80번 허용, Elastic IP, IGW와 기본 라우팅, Docker 포트 매핑, Caddy의 `respond /health "OK" 200`을 연결해 고정 응답으로 판정합니다. 방식 A의 소개 페이지도 같은 서버에서 확인할 수 있습니다.

| 대상 | 주소 | 기대 결과 |
|---|---|---|
| 평가용 HTTP (방식 B) | `http://<생성 때 출력한 IP>/health` | `200`, 본문 `OK` |
| 평가용 소개 페이지 (방식 A) | `http://<생성 때 출력한 IP>` | 소개 페이지 표시 |
| 평가용 HTTPS (보너스 1) | `https://b3-1.dmssh.store/health` | 정식 인증서로 `200`, 본문 `OK` |
| 평가용 도메인 HTTP | `http://b3-1.dmssh.store` | `308`로 HTTPS에 연결 |

아래 스크린샷은 **평가용 스택이 아니라 운영 스택의 2026-09-28 기록**입니다. 당시 방식 B의 접속 주소는 `http://52.78.251.4/health`였습니다. 평가용 스택의 접속·서버 내부·삭제 결과는 [2026-10-07 리허설 원본](docs/evidence/b3-1-rehearsal.txt)에 있습니다. 그 IP는 삭제 후 반납했으므로 평가 때는 새로 출력한 주소를 씁니다.

| 외부 접속: 노트북의 `curl -i` | 컨테이너 상태와 서버 내부 접속 |
|---|---|
| ![2026-09-28 운영 스택의 외부 HTTP 접속, 200과 OK](docs/evidence/terminal-external-access.png) | ![2026-09-28 운영 스택의 docker ps Up 상태와 localhost 접속](docs/evidence/terminal-server-docker-ps.png) |

| 방식 A: 공인 IP의 소개 페이지 | 보너스 1: 도메인 HTTPS |
|---|---|
| ![2026-09-28 운영 스택의 공인 IP 소개 페이지](docs/evidence/browser-http-ip.png) | ![2026-09-28 운영 스택의 HTTPS 소개 페이지](docs/evidence/browser-https-domain.png) |

주소·시각 띠는 캡처 때 덧붙인 것이며 페이지 자체에는 없습니다. [외부 접속 원본](docs/evidence/external-access.txt)과 [서버 내부 원본](docs/evidence/server-checks.txt)도 함께 보관합니다. `/health`의 고정 응답은 웹 서버까지 연결됐다는 증빙이며, 위키 검색 전체의 정상 동작을 뜻하지는 않습니다.

## 아키텍처

![AWS 배포 아키텍처: 외부 요청이 IGW, 퍼블릭 서브넷, 보안 그룹, EC2의 Caddy로 들어오고 라우팅 테이블의 기본 경로로 응답한다](docs/architecture.png)

그림은 운영 스택에 Caddy만 배포했던 시점의 네트워크입니다. 평가용 스택도 같은 VPC·서브넷·IGW·보안 그룹 구조를 쓰며, EC2 유형·IP·도메인이 위 비교표와 다릅니다. RAG 서비스까지 포함한 서버 내부 구성은 [검색 서버 설명](docs/project-overview.md#구성)에 있습니다. 그림의 [원본 JSON](docs/architecture.archify.json)과 [HTML 소스](docs/architecture.html)도 보관합니다.

1. 도메인으로 접속하면 DNS가 Elastic IP를 알려 줍니다. IP로 직접 접속할 때는 이 단계를 거치지 않습니다.
2. 요청이 Internet Gateway를 거쳐 퍼블릭 서브넷의 EC2로 들어옵니다. EC2에 연결된 Security Group이 출처와 목적지 포트를 확인합니다.
3. 호스트의 80·443 포트가 Docker 포트 매핑을 통해 Caddy 컨테이너에 연결됩니다.
4. 응답과 서버의 인터넷 요청은 서브넷 라우팅 테이블의 `0.0.0.0/0 → IGW` 경로로 나갑니다. 공인 IP만 있고 이 경로가 없으면 인터넷 연결을 완성할 수 없습니다.

| 구성 | 설정 | 구현·증빙 |
|---|---|---|
| 리전 / 가용 영역 | 서울 `ap-northeast-2` / `ap-northeast-2a` | [변수](infra/variables.tf) |
| VPC / Public Subnet | `10.0.0.0/16` / `10.0.1.0/24` | [Terraform](infra/main.tf) |
| IGW / Route Table | VPC에 IGW 연결, 서브넷에 기본 경로 연결 | [Terraform](infra/main.tf), [리허설 4절](docs/evidence/b3-1-rehearsal.txt) |
| 평가용 EC2 / EBS | `t3.micro`, Ubuntu 24.04 LTS, gp3 8GiB, 종료 시 볼륨 삭제 | [Terraform](infra/main.tf) |
| 공인 IP | 스택마다 Elastic IP 1개, 삭제 때 반납 | [출력 정의](infra/outputs.tf), [정리 기록](docs/cleanup-checklist.md#정리-실행-기록) |
| 서버 부팅 | Docker 설치·DNS 갱신·웹 서버 실행 | [평가용 cloud-init](infra/cloud-init-b3-1.yaml.tftpl) |

평가용 스택은 과제의 micro·8~10GiB 권장 사양을 따릅니다. 운영 스택은 로컬 임베딩 모델 두 프로세스와 DB·위키 서비스를 위해 8GiB 메모리와 20GiB 디스크를 사용합니다. 선택 근거와 측정은 [ADR-23](docs/adr/ADR-23.md), 디스크 문제와 조치는 [트러블슈팅 3절](docs/troubleshooting.md#3-같은-커밋을-다시-배포하다-디스크가-가득-참-m5)에 있습니다.

## 보안 그룹과 IAM

Security Group은 **어느 IP가 EC2의 어느 포트에 접속할 수 있는지**, IAM은 **누가 AWS 자원을 조회·생성·삭제할 수 있는지**를 정합니다.

| 방향 | 포트 | 출처·목적지 | 용도 |
|---|---|---|---|
| 인바운드 | TCP 80 | `0.0.0.0/0` | 외부 HTTP 접속, 인증서 발급, HTTPS 전환 |
| 인바운드 | TCP 443 | `0.0.0.0/0` | HTTPS |
| 인바운드 | TCP 22 | 운영자 공인 IP `/32` | SSH 배포·점검 |
| 아웃바운드 | 전체 | `0.0.0.0/0` | 패키지·이미지 다운로드, DNS·인증서 발급 등 |

전체 포트 인바운드는 없으며 DB·Redis·Caddy 관리 포트도 공개하지 않습니다. `ssh_cidr`는 기본값이 없고 `0.0.0.0/0`을 거부합니다. 평가 스크립트는 실행 시점의 운영자 IP를 넣습니다.

과제 자원은 관리자 권한 없는 IAM 사용자 `wiki-rag-deployer`로 생성·삭제합니다. [정책 파일](infra/iam/deployer-policy.json)은 서울 리전의 조회·생성·연결과 `project=wiki-rag-mcp` 태그가 있는 자원의 삭제를 허용합니다.

- [최소 권한 확인 기록](docs/evidence/iam-least-privilege.txt): 서울 EC2 조회는 허용, S3·다른 리전 EC2·IAM 사용자 목록은 거부.
- [삭제 권한 모의 실행](docs/evidence/cleanup-dry-run.txt): 프로젝트 자원 삭제 호출 12개는 허용, 태그 없는 기본 VPC 삭제는 거부. 이 기록은 권한 확인이며 실제 삭제 증빙은 리허설 기록입니다.
- 루트 CLI 세션을 잘못 가져온 문제와 IAM 사용자로 바로잡은 과정은 [트러블슈팅 1절](docs/troubleshooting.md#1-cli가-iam-사용자가-아니라-루트-계정으로-로그인됨)에 남겼습니다.

## 보너스: HTTPS와 Docker

| 항목 | 구현 | 증빙 |
|---|---|---|
| 도메인·HTTPS | Caddy가 Let's Encrypt 인증서를 발급·갱신. 도메인의 HTTP는 HTTPS로 전환 | [운영 스택의 인증서 검증 기록](docs/evidence/https-certificate.txt), 위 HTTPS 스크린샷 |
| 평가용 DNS | 가비아 CNAME → DuckDNS → 생성 때 받은 Elastic IP | [평가용 cloud-init](infra/cloud-init-b3-1.yaml.tftpl), [리허설](docs/evidence/b3-1-rehearsal.txt) |
| 이미지 | `caddy:2.11.4-alpine` | 위 `docker ps` 스크린샷 |
| 실행 방식 | 평가용은 cloud-init의 `docker run`, 운영은 [Docker Compose](deploy/compose.yaml) | [평가용 실행 명령](infra/cloud-init-b3-1.yaml.tftpl), [운영 Caddyfile](deploy/Caddyfile) |
| 포트 매핑 | 호스트 `80:80`, `443:443` | 위 서버 내부·외부 스크린샷 2장 |
| 재시작·볼륨 | `unless-stopped`, 설정·페이지는 읽기 전용, 인증서는 이름 있는 볼륨 | 같은 실행 정의 |

평가용 HTTPS 리허설은 시험 인증서를 사용했습니다. 운영 스택의 정식 인증서 기록과 섞어 평가용 정식 인증서 발급 완료로 주장하지 않습니다.

## 트러블슈팅과 리소스 정리

[트러블슈팅 보고서](docs/troubleshooting.md)에는 루트 CLI 로그인, 노트북의 간헐적 80번 연결 실패, Docker 빌드 중 디스크 부족, Caddyfile 변경 미반영을 기록했습니다. 외부 접속이 안 되면 라우팅·IGW 연결 → 보안 그룹 → 공인 IP·DNS → 서버 프로세스·로그를 확인하고, 다른 네트워크에서도 재현되는지 비교합니다. 포트를 넓히거나 관리자 권한을 붙이기 전에 실패 근거부터 확인합니다.

[정리 체크리스트](docs/cleanup-checklist.md)는 Terraform 상태와 `project`·`stack` 태그로 자원을 추적합니다. `infra/b3-1.sh down`은 DNS 값을 먼저 비우고 자원을 삭제한 뒤 [잔여 자원 점검](infra/check-leftovers.sh)을 실행합니다. EC2만 중지하면 EBS와 공인 IPv4가 남으므로 종료·삭제·반납을 각각 확인합니다.

- **삭제 증빙**: 2026-10-07 리허설 2회 모두 13개 삭제, EC2·EBS·Elastic IP·IGW·VPC를 포함한 점검 모두 0. [원본](docs/evidence/b3-1-rehearsal.txt).
- **평가 후 기록**: 평가 1~3회는 각 회차가 끝난 뒤 체크리스트에 채웁니다. 운영 스택의 삭제는 별도 절차입니다.
- **비용 확인**: 프리 티어 범위는 과제 제약이며, micro 사양만으로 비용 조건을 증명할 수 없습니다. 계정의 적용 범위·사용량과 정리 후 Billing 확인이 필요합니다. 현재 제출물에는 Billing 화면 증빙이 없습니다.

## 관련 코드와 문서

| 경로 | 내용 |
|---|---|
| [infra/](infra/) | Terraform, 평가 스택 실행, IAM 정책, 잔여 자원 점검 |
| [deploy/](deploy/) | 운영 Compose, Caddy 설정, 소개 페이지 |
| [검색 서버 설명](docs/project-overview.md) | 권한 검색 예시, 설계 결정, 목표와 측정, 알려진 위험, 관측성·부하 측정 |
| [설계서](docs/design.md) · [결정 기록](docs/adr/README.md) | 검색·권한·인덱싱·HTTP/OAuth·배포 설계 |
| [실험 기록](experiments/README.md) | M1~M6의 판정 기준·결과·원본 데이터 |
| [위키 서비스 계약](wiki-service/README.md) | Spring 위키 API, 이벤트, 인가 서버 |
