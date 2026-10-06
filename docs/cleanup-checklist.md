# 리소스 정리 체크리스트

실습에 만든 AWS 자원을 과금 위험 없이 지우는 순서와, 지웠다는 근거를 남기는 곳입니다.

## 추적 기준

- **모든 자원을 Terraform으로 만든다.** 콘솔에서 손으로 만든 자원이 없어야 `terraform destroy` 한 번으로 빠짐없이 지워진다. 무엇을 만들었는지는 `infra/main.tf`와 `terraform output resource_ids`가 목록이다.
- **모든 자원에 `project=wiki-rag-mcp` 태그를 붙인다.** Terraform 공급자의 `default_tags`로 붙이므로 빠뜨릴 수 없다. 루트 볼륨, Elastic IP, 키 페어까지 태그가 붙은 것을 확인했다.
- **지우기 권한도 이 태그에 묶는다.** 배포 사용자(`wiki-rag-deployer`)는 이 태그가 붙은 자원만 지울 수 있어서, 정리하다가 다른 자원(예: 서울 리전 기본 VPC)을 지울 수 없다(`infra/iam/deployer-policy.json`).
- **평가용 스택에는 `stack=b3-1` 태그를 더 붙인다.** 과제 평가 때마다 만들고 지우는 스택이다(README "평가 방식"). 운영 서버가 있어도 평가용 스택만 따로 셀 수 있다. Terraform 상태 파일(workspace)도 따로라서, 평가용 스택을 지워도 운영 서버에는 닿지 않는다.
- **정리가 끝났는지는 태그와 과금 기준 두 가지로 본다.** `infra/check-leftovers.sh`가 태그로 찾은 자원과, 태그와 상관없이 과금되는 것(연결 안 된 볼륨, 할당된 Elastic IP, NAT 게이트웨이)을 함께 센다. `STACK=b3-1`을 주면 평가용 스택만 세고, Elastic IP도 그 태그가 붙은 것만 센다.

## 정리 대상

두 스택이 같은 자원 13개를 만든다. 아래 ID는 운영 스택의 것이고, 평가용 스택의 ID는 만들 때마다 바뀌어 아래 "정리 실행 기록"과 증빙에 남긴다.

| 자원 | 운영 스택 ID | 남으면 과금되는 이유 | 정리 방식 |
|---|---|---|---|
| EC2 인스턴스 | `i-01edbaf39408d24ef` | 실행 시간만큼 과금. 중지해도 볼륨과 공인 IP는 계속 과금 | 종료(terminate) |
| EBS 볼륨 (루트 20GB) | `vol-0c3df9448c49869d0` | 연결이 끊겨도 용량만큼 매달 과금 | 인스턴스 종료와 함께 삭제(`DeleteOnTermination=true`) |
| Elastic IP | `eipalloc-0c18065934a830a35` (52.78.251.4) | 공인 IPv4는 인스턴스에 붙어 있든 아니든 시간당 과금 | 연결 해제 뒤 반납(release) |
| 인터넷 게이트웨이 | `igw-023e2ba76f935ad15` | 자체 과금은 없지만 VPC 삭제를 막음 | VPC에서 분리 뒤 삭제 |
| 라우팅 테이블 | `rtb-031342c5cabae623b` | 과금 없음, VPC 삭제를 막음 | 서브넷 연결 해제 뒤 삭제 |
| 보안 그룹 | `sg-0ab61932333ffdfc3` | 과금 없음, VPC 삭제를 막음 | 삭제 |
| 서브넷 | `subnet-05d8c7e84536436cd` | 과금 없음, VPC 삭제를 막음 | 삭제 |
| VPC | `vpc-0b900133c37418e1b` | 과금 없음 | 마지막에 삭제 |
| 키 페어 | `wiki-rag-operator` | 과금 없음 | 삭제 |

만들지 않은 것: NAT 게이트웨이, 로드 밸런서(ELB/ALB), RDS. `check-leftovers.sh`가 NAT 게이트웨이도 세서 0인지 확인한다.

AWS 밖에도 지울 것이 있다. 반납한 Elastic IP는 다른 고객에게 갈 수 있어서, 그 IP를 가리키는 DNS 값이 남으면 내 도메인이 남의 서버를 가리킨다.

| 스택 | DNS | 정리 시점 |
|---|---|---|
| 평가용 | DuckDNS `b3-1.duckdns.org`의 IP | 평가마다, `infra/b3-1.sh down`이 비움 |
| 평가용 | 가비아 CNAME `b3-1` (`b3-1.duckdns.org`를 가리킴) | 평가가 모두 끝난 뒤 한 번 |
| 운영 | 가비아 A 레코드 `mcp`, `auth` | 운영 스택을 지울 때 |

## 정리 순서와 이유

`terraform destroy`는 자원 사이의 의존 관계를 거꾸로 따라 지운다. 손으로 지울 때도 같은 순서여야 한다.

1. **Elastic IP 연결 해제와 반납.** 공인 IP가 매핑된 채로는 인터넷 게이트웨이를 분리할 수 없다.
2. **EC2 종료.** 루트 볼륨이 함께 지워진다. 인스턴스의 네트워크 인터페이스가 없어져야 서브넷과 보안 그룹을 지울 수 있다.
3. **보안 그룹 규칙과 보안 그룹, 라우팅 테이블 연결과 라우팅 테이블 삭제.**
4. **인터넷 게이트웨이 분리와 삭제, 서브넷 삭제.**
5. **VPC 삭제.** VPC 안에 남은 것이 있으면 `DependencyViolation`으로 실패하므로, 이 단계가 성공하면 안쪽이 모두 지워졌다는 뜻이다.

## 절차

### 평가용 스택 (평가마다)

1. 배포 사용자로 로그인했는지 본다(`aws sts get-caller-identity --profile wiki-rag`가 `user/wiki-rag-deployer`).
2. 평가자 앞에서 지운다. 스크립트가 지울 목록을 보여 주고 확인(`yes`)을 받은 뒤 지우고, 남은 자원을 세고, DuckDNS 값을 비운다.

   ```
   infra/b3-1.sh down
   ```

   `Destroy complete! Resources: 13 destroyed.`와 `check-leftovers.sh`의 모든 줄 0, `DuckDNS ... 값 비움 (지금 조회 값: 없음)`이 나와야 한다.
3. 출력을 아래 "정리 실행 기록"에 적는다.

실제 인증서는 같은 이름(`b3-1.dmssh.store`)으로 7일에 5장까지 발급된다. 연습으로 만들 때는 `infra/b3-1.sh up --staging`(시험 인증서)을 쓰고, 실제 발급은 평가 때만 한다.

### 평가가 모두 끝난 뒤

1. 가비아 DNS에서 CNAME `b3-1`을 지운다.
2. 그다음 DuckDNS에서 `b3-1` 이름을 지운다. 순서가 반대면, CNAME이 남아 있는 동안 남이 그 DuckDNS 이름을 가져가 `b3-1.dmssh.store`로 들어온 요청을 받을 수 있다.
3. 다음 날 결제 콘솔에서 평가용 스택의 EC2, EBS, 공인 IPv4 사용량이 더 늘지 않는지 본다.

### 운영 스택

1. 배포 사용자로 로그인했는지 본다. 루트로 지우면 태그 조건이 걸리지 않는다.

   ```
   aws sts get-caller-identity --profile wiki-rag   # ...:user/wiki-rag-deployer
   ```

2. 지울 목록을 먼저 본다. 위 표의 자원만 있어야 한다(보안 그룹 규칙 4개와 라우팅 테이블 연결을 따로 세어 Terraform 자원 13개).

   ```
   terraform -chdir=infra plan -destroy
   ```

3. 지운다.

   ```
   terraform -chdir=infra destroy
   ```

4. 남은 자원이 없는지 본다. 모든 줄이 0이어야 한다.

   ```
   AWS_PROFILE=wiki-rag infra/check-leftovers.sh
   ```

5. 가비아 DNS에서 `mcp`와 `auth` A 레코드를 지운다. 반납한 Elastic IP는 다른 AWS 고객에게 다시 할당될 수 있어서, 레코드를 두면 `mcp.dmssh.store`와 `auth.dmssh.store`가 남의 서버를 가리킨다. `www` CNAME(Vercel)은 이 과제와 무관하므로 그대로 둔다.
6. 루트 계정으로 결제 콘솔(Billing and Cost Management)에 들어가 서울 리전 EC2, EBS, 공인 IPv4 항목이 더 늘지 않는지 본다. 사용량은 몇 시간 늦게 반영되므로 다음 날 한 번 더 본다.
7. README를 운영이 끝난 상태로 고친다. 서버가 없는데 주소가 살아 있는 것처럼 두지 않기 위해서다.
   - 맨 위 "운영 서버" 주소를 "운영 기록"으로 바꾼다.
   - B3-1 절에 남은 운영 IP(`52.78.251.4`)와 도메인을 "운영 당시의 주소"라고 적는다. 반납한 IP는 남의 서버에 닿을 수 있다.
   - B3-1 절의 낡은 문장 두 개를 고친다. "부하 측정 때 다시 잽니다"는 이미 관측성 절에 측정값이 있고, "확대해 볼 수 있는 HTML"은 GitHub에서 소스만 보여 `docs/architecture.html`과 함께 지운다.

## 정리하지 않는 것

| 대상 | 이유 |
|---|---|
| 서울 리전 기본 VPC | AWS가 계정마다 만들어 둔 것이고 과금이 없다. 배포 사용자 권한으로는 지울 수도 없다(아래 모의 실행) |
| IAM 사용자 `wiki-rag-deployer` | 과금이 없고 M5에서 같은 인프라를 다시 만들 때 쓴다. 프로젝트를 끝낼 때 콘솔 접근과 CLI 로그인 권한을 없앤다 |
| 예산 경보 `wiki-rag-monthly` | 과금이 없고, 정리 뒤에 뜻밖의 비용이 생기면 알려 준다 |

## 권한 모의 실행 (정리 전)

`terraform destroy`가 부르는 삭제 호출 12개를 배포 사용자로 `--dry-run` 해서 모두 허용되는 것과, 태그 없는 기본 VPC 삭제는 거부되는 것을 확인했다. `--dry-run`은 자원을 바꾸지 않고 권한만 판정한다. 결과는 `docs/evidence/cleanup-dry-run.txt`에 있다.

## 정리 실행 기록

### 평가용 스택

평가항목은 한 항목 안에서 "웹 서버가 실행 중인가"와 "정리 5종 완료"를 함께 본다. 그래서 평가마다 만들어 보여 주고, 평가 끝에 그 자리에서 지운 뒤 한 줄씩 채운다.

| 회차 | 일시 | `terraform destroy` | `check-leftovers.sh` (STACK=b3-1) | DuckDNS 값 | 근거 |
|---|---|---|---|---|---|
| 리허설 1 | 2026-10-07 | 13개 삭제, 37초 | 모두 0 | 없음 | [`evidence/b3-1-rehearsal.txt`](evidence/b3-1-rehearsal.txt) |
| 리허설 2 | 2026-10-07 | 13개 삭제, 37초 | 모두 0 | 없음 (권한 서버 조회) | 같은 파일 7절 |
| 평가 1 | | | | | |
| 평가 2 | | | | | |
| 평가 3 | | | | | |

### 운영 스택

운영 서버는 과제 평가와 따로, 운영(M5, M6)을 끝낼 때 지운다. 지운 뒤 아래를 채운다.

| 항목 | 확인 방법 | 결과 |
|---|---|---|
| 실행 일시와 실행 주체 | `aws sts get-caller-identity` | 실행 전 |
| `terraform destroy` | 출력 마지막 줄 `Destroy complete! Resources: 13 destroyed.` | 실행 전 |
| EC2 종료 | `check-leftovers.sh` 0, 인스턴스 상태 `terminated` | 실행 전 |
| EBS 볼륨 삭제 | `check-leftovers.sh` 프로젝트 볼륨 0, 연결 안 된 볼륨 0 | 실행 전 |
| Elastic IP 반납 | `check-leftovers.sh` Elastic IP 0 | 실행 전 |
| 인터넷 게이트웨이 삭제 | `check-leftovers.sh` 0 | 실행 전 |
| VPC, 서브넷, 라우팅 테이블, 보안 그룹 삭제 | `check-leftovers.sh` 모두 0 | 실행 전 |
| DNS 레코드 삭제 | `dig +short A mcp.dmssh.store`와 `dig +short A auth.dmssh.store`가 빈 값 | 실행 전 |
| 결제 콘솔 | 다음 날 서울 리전 과금 항목이 늘지 않음 (스크린샷) | 실행 전 |
| README 정리 | 운영 주소와 IP가 링크로 남지 않음, `docs/architecture.html` 삭제 | 실행 전 |
