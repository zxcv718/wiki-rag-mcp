# wiki-rag-mcp

사내 위키 문서를 **사용자 권한에 맞게** 검색해, LLM 에이전트가 MCP 도구로 호출할 수 있게 하는 서버입니다. 답변은 만들지 않고 근거가 되는 문서 조각만 돌려주며, 권한 없는 문서는 검색 단계에서부터 제외합니다.

- 설계: [`docs/design.md`](docs/design.md), 결정 기록: [`docs/adr/`](docs/adr/README.md)
- 진행: M1~M4 완료(MCP 서버, 골든셋 평가, 위키 서비스와 증분 인덱싱, 권한 pre-filter와 CI), M5(HTTP 전송, OAuth, 부하 측정, 관측성) 진행 중

프로젝트 전체 소개와 데모는 M6에서 이 문서에 정리합니다. 아래는 서버를 AWS에 올린 기록이며, 코디세이 과제 B3-1의 제출 문서를 겸합니다.

## 클라우드 배포 (B3-1)

### 접속 정보와 검증 방식

**검증 방식: B** (`GET http://<퍼블릭 IP>/health`가 200과 고정 응답 `OK`를 돌려줌). 방식 A(브라우저로 `http://<퍼블릭 IP>`)도 함께 확인했습니다.

| 주소 | 기대 응답 |
|---|---|
| http://52.78.251.4/health | `200 OK`, 본문 `OK` (방식 B) |
| http://52.78.251.4 | 소개 페이지 (방식 A) |
| https://mcp.dmssh.store/health | HTTPS로 `200`, 본문 `OK` (보너스 1) |
| http://mcp.dmssh.store | `308`으로 HTTPS 주소로 넘김 (보너스 1) |

과제를 마치면 [정리 체크리스트](docs/cleanup-checklist.md)대로 자원을 지우므로, 그 뒤에는 위 주소가 닿지 않습니다. 접속 결과는 아래 스크린샷과 [`docs/evidence/`](docs/evidence/)의 원래 출력으로 남겼습니다.

| 방식 B: 노트북 터미널에서 `curl -i` | 방식 B: 브라우저 |
|---|---|
| ![외부에서 curl로 /health 호출](docs/evidence/terminal-external-access.png) | ![브라우저로 /health 접속](docs/evidence/browser-http-ip-health.png) |

| 방식 A: 브라우저로 `http://52.78.251.4` | 보너스 1: `https://mcp.dmssh.store` |
|---|---|
| ![브라우저로 퍼블릭 IP 접속](docs/evidence/browser-http-ip.png) | ![도메인 HTTPS 접속](docs/evidence/browser-https-domain.png) |

브라우저 스크린샷 위쪽의 주소·시각 띠는 캡처할 때만 덧붙인 것이고, 페이지 자체에는 없습니다.

### 아키텍처

![AWS 배포 아키텍처](docs/architecture.png)

사용자는 가비아 DNS에서 `mcp.dmssh.store`의 주소(Elastic IP)를 받아 요청을 보냅니다. 요청은 인터넷 게이트웨이를 지나 퍼블릭 서브넷의 EC2에 닿고, 보안 그룹이 허용한 80·443만 들어와 Caddy 컨테이너가 응답합니다. 운영자의 SSH(22)는 지정한 IP 한 곳에서만 들어옵니다. 다이어그램 원본은 [`docs/architecture.archify.json`](docs/architecture.archify.json)이고, 확대해 볼 수 있는 HTML은 [`docs/architecture.html`](docs/architecture.html)입니다.

| 구성 | 값 | 코드 |
|---|---|---|
| 리전 | 서울 `ap-northeast-2` (가용 영역 `ap-northeast-2a`) | `infra/variables.tf` |
| VPC | `10.0.0.0/16`, 이 프로젝트 전용 | `infra/main.tf` |
| 퍼블릭 서브넷 | `10.0.1.0/24` | `infra/main.tf` |
| 인터넷 게이트웨이와 라우팅 | 서브넷의 라우팅 테이블에 `0.0.0.0/0` 대상 인터넷 게이트웨이 | `infra/main.tf` |
| EC2 | `m7i-flex.large`, Ubuntu 24.04, 루트 볼륨 gp3 20GB(암호화), 메타데이터는 IMDSv2만 | `infra/main.tf`, `infra/cloud-init.yaml` |
| 공인 IP | Elastic IP `52.78.251.4` (다시 시작해도 주소가 바뀌지 않아 DNS 레코드를 고정할 수 있음) | `infra/main.tf` |
| 웹 서버 | Caddy 2.11.4 컨테이너 | `deploy/` |

인프라는 모두 Terraform으로 만들었습니다. 과제를 마치면 `terraform destroy` 한 번으로 지우고, M5에서 같은 구성을 다시 만들기 위해서입니다. 결정 이유와 다른 선택지(오라클 무료 등급, GCP, 유료 VPS) 비교는 [ADR-23](docs/adr/ADR-23.md)에 있습니다.

### 인스턴스와 볼륨을 권장보다 크게 잡은 이유

과제는 micro 인스턴스와 8~10GiB 볼륨을 권장하지만, 이 서버는 `m7i-flex.large`(vCPU 2개, 메모리 8GiB)와 20GB를 씁니다.

- 이 서버는 검색 질의를 임베딩하려고 모델(BAAI/bge-m3)을 프로세스 안에 올립니다. 모델을 올린 프로세스 하나가 최대 약 2.2GB였고, 검색 서버와 인덱서 두 프로세스에 DB·위키 서비스까지 합치면 5~6GB가 필요합니다. micro(1GiB)에서는 모델을 올릴 수 없습니다.
- 2025년 7월 15일 이후 만든 계정은 무료 플랜 대상 유형이 `t3.micro`, `t3.small`, `t4g.micro`, `t4g.small`, `c7i-flex.large`, `m7i-flex.large`입니다([AWS 문서](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html)). 이 중 메모리 5GB 이상은 `m7i-flex.large`뿐입니다. 비용은 무료 플랜 크레딧에서 나갑니다.
- 볼륨 20GB는 모델 파일(약 2.2GB)과 파이썬·PyTorch가 든 Docker 이미지를 담기 위한 크기입니다.
- 같은 유형의 인스턴스에서 질의 임베딩을 실제로 쟀습니다(골든셋 질문 60개, p50 225ms). 측정 방법과 결과는 ADR-23에 있습니다.

### 보안 그룹

| 방향 | 포트 | 출처 | 이유 |
|---|---|---|---|
| 인바운드 | 80 (HTTP) | `0.0.0.0/0` | 외부 접속 검증, Let's Encrypt 인증서 발급(HTTP-01), HTTPS로 넘김 |
| 인바운드 | 443 (HTTPS) | `0.0.0.0/0` | 보너스 1 |
| 인바운드 | 22 (SSH) | 운영자 IP `/32` 하나 | 배포와 점검. Terraform 변수 `ssh_cidr`에 기본값이 없고, `0.0.0.0/0`을 넣으면 검증에서 막힘 |
| 아웃바운드 | 전체 | `0.0.0.0/0` | 패키지 설치, 이미지 내려받기, 인증서 발급 |

전체 포트(0-65535)를 여는 인바운드 규칙은 없습니다. DB, Redis, 위키 서비스 포트도 밖으로 열지 않습니다.

### IAM 최소 권한

과제 인프라(위 VPC와 그 안의 자원)는 모두 IAM 사용자 `wiki-rag-deployer`로 만들었고 지우는 것도 이 사용자로 합니다(관리자 권한 없음). 루트 계정은 이 사용자와 정책을 만들 때와 결제 확인에만 씁니다. 과제를 시작하기 전 배포처를 고르려고 루트로 띄웠던 측정용 인스턴스는 과제 인프라를 만들기 전에 지웠습니다. 정책은 [`infra/iam/deployer-policy.json`](infra/iam/deployer-policy.json)이고 세 부분입니다.

| 문장 | 허용 범위 |
|---|---|
| `ReadEc2AndVpcInSeoul` | 서울 리전의 EC2·VPC 조회 |
| `BuildNetworkAndServerInSeoul` | 서울 리전에서 VPC, 서브넷, 인터넷 게이트웨이, 라우팅, 보안 그룹, 인스턴스, Elastic IP, 키 페어를 만들고 연결하기 |
| `TearDownOnlyThisProject` | 삭제와 종료는 `project=wiki-rag-mcp` 태그가 붙은 자원에만 |

- 확인 결과: 서울 리전 EC2 조회는 허용되고 S3 목록, 다른 리전(버지니아) EC2, IAM 사용자 목록은 거부됩니다([`docs/evidence/iam-least-privilege.txt`](docs/evidence/iam-least-privilege.txt)).
- 삭제 권한은 `--dry-run`으로 확인했습니다. 이 프로젝트 자원의 삭제 호출 12개는 허용되고, 태그 없는 기본 VPC 삭제는 거부됩니다([`docs/evidence/cleanup-dry-run.txt`](docs/evidence/cleanup-dry-run.txt)).
- 이 밖에 CLI 로그인(`aws login`)용 관리형 정책 `SignInLocalDevelopmentAccess`를 붙였습니다. 로그인 토큰 발급 권한만 있어 서비스 권한은 늘지 않습니다.

### 보너스 1: 도메인과 HTTPS

- 도메인: 가비아에서 산 `dmssh.store`에 A 레코드 `mcp`를 만들어 Elastic IP를 가리키게 했습니다.
- 인증서: Caddy가 Let's Encrypt에서 HTTP-01 방식으로 자동 발급하고 만료 전에 갱신합니다. 발급된 인증서는 `CN=mcp.dmssh.store`, 발급자 Let's Encrypt, 유효 기간 2026-09-28~2026-12-27이고, 인증서 체인 검증도 통과합니다([`docs/evidence/https-certificate.txt`](docs/evidence/https-certificate.txt)).
- 도메인으로 온 HTTP 요청은 `308`로 HTTPS 주소에 넘깁니다. IP로 온 HTTP 요청은 인증서를 쓸 수 없으므로 그대로 응답합니다(방식 A·B 검증용). 설정은 [`deploy/Caddyfile`](deploy/Caddyfile)입니다.

### 보너스 2: Docker 배포

| 항목 | 내용 |
|---|---|
| 이미지 | `caddy:2.11.4-alpine` (Docker Hub 공식 이미지) |
| 포트 매핑 | 호스트 `80` 대 컨테이너 `80`, 호스트 `443` 대 컨테이너 `443` |
| 볼륨 | 설정 `deploy/Caddyfile`, 페이지 `deploy/site/`(읽기 전용), 인증서 보관용 이름 있는 볼륨 `caddy-data`·`caddy-config` |
| 재시작 | `restart: unless-stopped` (서버를 다시 켜도 컨테이너가 다시 뜸) |
| 정의 | [`deploy/compose.yaml`](deploy/compose.yaml) |

실행 방식: 서버의 Docker는 첫 부팅 때 cloud-init이 설치합니다(`infra/cloud-init.yaml`). 노트북에서 아래를 실행하면 `deploy/`를 서버에 복사하고 `docker compose up -d`로 컨테이너를 띄웁니다.

```bash
deploy/push.sh "$(terraform -chdir=infra output -raw public_ip)"
```

서버에서 직접 띄울 때는 `deploy/.env`에 `DOMAIN=mcp.dmssh.store`를 적고 `cd ~/deploy && docker compose up -d`입니다. 인증서를 이름 있는 볼륨에 두는 이유는, 컨테이너를 다시 만들 때마다 새로 발급받으면 Let's Encrypt 발급 한도에 걸리기 때문입니다.

| 서버 안: `docker ps`와 `curl http://localhost` | 외부: `/health` 호출 |
|---|---|
| ![docker ps와 서버 안 curl](docs/evidence/terminal-server-docker-ps.png) | ![외부에서 /health 호출](docs/evidence/terminal-external-access.png) |

서버 안 확인 결과: 컨테이너 `Up`, `curl http://localhost` 200, `/health` 본문 `OK`, 서버에서 바깥(example.com)으로 나가는 요청 200([`docs/evidence/server-checks.txt`](docs/evidence/server-checks.txt)).

### 다시 만들기

1. 배포용 IAM 사용자로 로그인합니다. `aws sts get-caller-identity --profile wiki-rag`가 `user/wiki-rag-deployer`여야 합니다.
2. `infra/terraform.tfvars.example`을 `infra/terraform.tfvars`로 복사하고 `ssh_cidr`에 내 IP(`/32`)를 넣습니다.
3. `terraform -chdir=infra init && terraform -chdir=infra apply`로 자원을 만듭니다. 출력의 `public_ip`가 Elastic IP입니다.
4. 도메인을 쓰면 DNS에 A 레코드를 넣고, 서버의 `~/deploy/.env`에 `DOMAIN`을 적습니다.
5. `deploy/push.sh <public_ip>`로 컨테이너를 띄웁니다.

### 관련 문서

- [트러블슈팅 보고서](docs/troubleshooting.md): CLI가 루트로 로그인된 건, 노트북에서만 80번이 가끔 끊긴 건
- [리소스 정리 체크리스트](docs/cleanup-checklist.md): 정리 순서와 이유, 남은 자원 확인 스크립트(`infra/check-leftovers.sh`)
- [ADR-23](docs/adr/ADR-23.md): 배포처와 Terraform을 고른 이유
