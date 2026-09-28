# ADR-23. 배포는 AWS 서울 리전 EC2 한 대, 인프라는 Terraform으로 관리

- 상태: 확정
- 주요 대안: 오라클 무료 등급, GCP 크레딧, 유료 VPS
- 출처: 설계서 8. 운영과 성능

M5의 지연·부하 측정과 데모에는 인터넷에서 닿는 서버가 필요합니다. 비용 제약(월 수만 원, 0장) 안에서 고르고, 같은 인프라로 코디세이 과제 B3-1(서울 리전, 전용 VPC, 최소 권한 IAM, 끝나면 자원 정리)도 함께 냅니다. 후보마다 실제 CPU에서 질의 임베딩(골든셋 질문 60개, float32)을 쟀습니다.

| 선택지 | 사양과 비용 | 질의 임베딩 p50 | 걸리는 점 |
|---|---|---|---|
| 오라클 무료 등급 (A1) | Ampere Altra 2코어, 12GB, 기한 없음 | 312ms (같은 조건의 리눅스 ARM 컨테이너로 추정) | 2026년 6월 무료 한도가 절반으로 줄었고, AMX 같은 행렬 가속이 없음 |
| GCP 무료 체험 | 사양 자유, $300를 90일 | 재지 않음 | 계정에 따라 선결제 요구, 90일 뒤 자원 정지 |
| 유료 VPS (Hetzner CX43) | x86 8코어, 16GB, 월 €15.99 | 재지 않음 | 서버가 유럽에 있음, 비용 발생 |
| **AWS 무료 플랜 m7i-flex.large (선택)** | Xeon 8488C 물리 1코어(2스레드), 8GB, 크레딧 $200 | 225ms (bf16이면 90ms) | 물리 코어 1개, flex라 기본 성능이 vCPU당 40% |

- **결정**:
  - 서울 리전에 전용 VPC 하나, 퍼블릭 서브넷 하나(라우팅 0.0.0.0/0이 인터넷 게이트웨이를 가리킴), EC2 한 대, Elastic IP를 둡니다.
  - 보안 그룹은 80·443을 전체에, 22는 운영자 IP에만 엽니다. DB, Redis, 위키 서비스 포트는 밖으로 열지 않습니다.
  - 웹 서버는 Caddy입니다. `mcp.dmssh.store`에 Let's Encrypt 인증서를 자동으로 받고 갱신하며, 앱은 Docker로 띄웁니다(`deploy/`).
  - 인프라는 Terraform(`infra/`)으로 만들고 지웁니다. 실행은 루트가 아닌 최소 권한 IAM 사용자로 합니다. 이 사용자는 서울 리전의 EC2·VPC·보안 그룹 작업만 할 수 있고, 지우기는 `project=wiki-rag-mcp` 태그가 붙은 자원에만 됩니다(`infra/iam/deployer-policy.json`).
- **결정 이유**:
  - 크레딧으로 비용이 들지 않고, 서울 리전이라 한국에서 재는 지연이 실제 사용과 가깝습니다.
  - 후보 중 유일하게 실제 인스턴스에서 재 봤고, 메모리 8GB가 실측한 필요량(모델을 올린 프로세스 두 개 약 4.4GB와 DB·위키 약 0.6GB)을 담습니다.
  - AMX가 있어, 임베딩 예산(100ms)을 넘는 float32 대신 bf16을 검토할 여지가 있습니다(ADR-11 재판정 대상).
  - Terraform은 과제 끝에 자원을 한 번에 지우고 M5를 위해 같은 구성으로 다시 만드는 데 필요합니다. 네트워크 구성이 코드로 남아 설명의 근거가 되기도 합니다.
- **감수한 비용**:
  - 물리 코어가 하나라 동시 처리량이 작습니다. float32로는 질의 하나에 0.2초 넘게 걸려 동시 50요청 목표(1장)를 못 맞출 수 있습니다.
  - 크레딧은 24시간 켜 두면 두 달쯤 가고, 무료 플랜은 2027년 3월 28일에 끝납니다. 그 뒤에도 데모를 띄워 두려면 옮겨야 합니다.
  - Terraform 상태 파일을 로컬에 둡니다. 한 사람이 한 곳에서만 돌리기 때문입니다.
- **재검토 조건**: 부하 테스트에서 동시 50요청의 오류율이나 검색 p95 목표를 못 맞추면 bf16 재판정(ADR-11)과 코어 증설 중에서 고릅니다. 크레딧이 떨어지기 전에 장기 호스팅처를 다시 정합니다.
- **참고**: [AWS 무료 플랜](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html), [EC2 무료 대상 유형](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html), [M7i-flex](https://aws.amazon.com/ec2/instance-types/m7i/), [오라클 무료 자원](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm), [Caddy 자동 HTTPS](https://caddyserver.com/docs/automatic-https)
