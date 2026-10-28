# 리소스 정리 체크리스트

실습에 만든 AWS 자원을 과금 위험 없이 지우는 순서와, 지웠다는 근거를 남기는 곳입니다.

## 추적 기준

- **모든 자원을 Terraform으로 만든다.** 콘솔에서 손으로 만든 자원이 없어야 `terraform destroy` 한 번으로 빠짐없이 지워진다. 무엇을 만들었는지는 `infra/main.tf`와 `terraform output resource_ids`가 목록이다.
- **모든 자원에 `project=wiki-rag-mcp` 태그를 붙인다.** Terraform 공급자의 `default_tags`로 붙이므로 빠뜨릴 수 없다. 루트 볼륨, Elastic IP, 키 페어까지 태그가 붙은 것을 확인했다.
- **지우기 권한도 이 태그에 묶는다.** 배포 사용자(`wiki-rag-deployer`)는 이 태그가 붙은 자원만 지울 수 있어서, 정리하다가 다른 자원(예: 서울 리전 기본 VPC)을 지울 수 없다(`infra/iam/deployer-policy.json`).
- **평가용 스택에는 `stack=b3-1` 태그를 더 붙인다.** 과제 평가 때마다 만들고 지우는 스택이다(README "평가 방식"). 운영 서버가 있어도 평가용 스택만 따로 셀 수 있다. Terraform 상태 파일(workspace)도 따로라서, 평가용 스택을 지워도 운영 서버에는 닿지 않는다.
- **정리가 끝났는지는 태그와 과금 기준 두 가지로 본다.** `infra/check-leftovers.sh`가 태그로 찾은 자원과, 태그와 상관없이 과금되는 것(연결 안 된 볼륨, 할당된 Elastic IP, NAT 게이트웨이)을 함께 센다. `STACK=b3-1`을 주면 평가용 스택만 세고, Elastic IP도 그 태그가 붙은 것만 센다.

## 정리 대상

두 스택이 같은 자원 13개를 만든다. 아래 ID는 2026-10-28에 지운 운영 스택의 것이다. 다시 만든 운영 스택의 ID는 `terraform -chdir=infra output resource_ids`로 본다. 평가용 스택의 ID는 만들 때마다 바뀌어 아래 "정리 실행 기록"과 증빙에 남긴다.

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
2. 평가자 앞에서 지운다. 스크립트가 먼저 DuckDNS 값을 비우고, 지울 목록을 보여 주고 확인(`yes`)을 받은 뒤 지우고, 남은 자원을 센다. DNS를 먼저 비우는 이유는, 지우기나 점검이 중간에 실패해도 도메인이 반납한 IP를 가리키지 않게 하기 위해서다.

   ```
   infra/b3-1.sh down
   ```

   `DuckDNS ... 값 비움 (권한 서버 조회 값: 없음)`, `Destroy complete! Resources: 13 destroyed.`, `check-leftovers.sh`의 모든 줄 0이 나와야 한다.
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
7. [README](../README.md)와 [검색 서버 설명](project-overview.md)을 운영이 끝난 상태로 고친다. 서버가 없는데 주소가 살아 있는 것처럼 두지 않기 위해서다.
   - README의 "평가 방식" 아래 스택 비교표를 갱신하고, "제출물과 증빙"에 운영 스택의 삭제 일시와 근거를 적는다.
   - 검색 서버 설명의 "운영 서버"를 "운영 기록"으로 바꾼다. 반납한 IP와 도메인은 운영 당시 주소로만 남기고 접속 링크는 없앤다.
   - `docs/architecture.html`을 지울 때는 README의 HTML 소스 링크도 함께 지운다.

### 운영 스택 다시 만들기 (삭제 뒤)

지운 뒤 서버가 다시 필요하면 같은 코드로 새로 만든다. 인프라와 배포는 [검색 서버 설명의 "운영 스택 배포"](project-overview.md#운영-스택-배포) 1~6단계를 따른다. 지운 뒤라서 달라지는 점과 더 할 일은 아래와 같다. 2026-10-28 정리 직후 이 절차로 다시 만들었다(배포 208초, 시드 문서 200개).

| 대상 | 지운 뒤 | 할 일 |
|---|---|---|
| Elastic IP | 반납해서 `52.78.251.4`는 돌아오지 않는다. 새 IP를 받는다 | 가비아의 `mcp`, `auth` A 레코드 값을 새 IP로 고친다. 레코드를 새로 만들 필요는 없다. Caddy가 인증서를 받으려면 이 이름이 새 서버로 와야 하므로 `push.sh` 전에 고친다 |
| 서버 비밀 (`deploy/.env`, `deploy/secrets/`) | 서버에만 있었으므로 함께 사라진다 | `push.sh`가 부르는 `init-secrets.sh`가 새로 만든다. 예전 토큰은 모두 무효라서 Claude Code 커넥터와 데모 에이전트는 다시 로그인한다. 데모 비밀번호도 바뀌므로 서버의 `deploy/.env`에서 확인한다 |
| 위키 DB | 백업이 없어 사라진다 | 가상 위키를 다시 옮긴다(아래 1). `membership.sh`로 바꾼 그룹 멤버십은 원래 값으로 돌아간다 |
| 검색 인덱스 | 사라진다 | 시드가 문서마다 이벤트를 내고, 워커가 처음부터 다시 색인한다. 2 vCPU에서 20분 안팎 걸린다 |
| HTTPS 인증서 | Caddy 볼륨과 함께 사라진다 | Caddy가 처음 뜰 때 새로 받는다. 같은 이름으로 7일에 5장까지 받을 수 있다 |
| SSH 허용 IP | `infra/terraform.tfvars`에 있는 값을 쓴다 | 지금 IP와 다르면 고친 뒤 apply한다 |

지운 직후 바로 다시 만들 때는 5단계에서 A 레코드를 지우지 않고, 새 IP가 나오면 값만 고쳐도 된다. 다만 그 사이에는 레코드가 반납한 IP를 가리키므로, 지우기와 다시 만들기 사이를 비워 두지 않는다.

`push.sh`가 끝나면 아래를 이어서 한다.

1. 가상 위키를 옮긴다. `문서 200개`가 출력되어야 한다.

   ```
   ssh -i ~/.ssh/wiki-rag-m5.pem ubuntu@<public_ip> \
     'cd ~/wiki-rag-mcp/deploy && docker compose --profile tools run --rm seed'
   ```

2. 색인이 끝났는지 본다. 결과가 200이면 끝난 것이다.

   ```
   ssh -i ~/.ssh/wiki-rag-m5.pem ubuntu@<public_ip> \
     'cd ~/wiki-rag-mcp/deploy && docker compose exec -T postgres psql -U wiki -d wiki_search -tAc "select count(*) from wiki_chunks_doc_state where not deleted"'
   ```

3. `https://mcp.dmssh.store/health`가 `OK`를 돌려주는지 확인한다. 이어서 Claude Code 커넥터로 다시 로그인해 `search_wiki`가 근거를 돌려주는지 확인한다.

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

2026-10-28에 과제 명세대로 운영 스택을 모두 지우고, 바로 이어서 같은 코드로 다시 만들었다(위 "운영 스택 다시 만들기"). 지운 자원의 ID는 위 "정리 대상" 표와 삭제 출력에 있다. 운영 자체를 끝낼 때는 이 절차를 다시 하고 DNS 레코드 삭제와 문서 정리까지 한다.

| 항목 | 확인 방법 | 결과 |
|---|---|---|
| 실행 일시와 실행 주체 | `aws sts get-caller-identity` | 2026-10-28 19:52 KST, `user/wiki-rag-deployer` |
| 지울 목록 | `terraform plan -destroy` | `Plan: 0 to add, 0 to change, 13 to destroy.` [`evidence/prod-plan-destroy.txt`](evidence/prod-plan-destroy.txt) |
| `terraform destroy` | 출력 마지막 줄 `Destroy complete! Resources: 13 destroyed.` | 13개 삭제, 37초. [`evidence/prod-destroy.txt`](evidence/prod-destroy.txt) |
| EC2 종료 | `check-leftovers.sh` 0, 인스턴스 상태 `terminated` | 0, 콘솔에서 `i-01edbaf39408d24ef` 종료됨 (다시 만든 `i-06a3c904c6c3ea3e5`는 실행 중). [`evidence/prod-ec2-terminated.png`](evidence/prod-ec2-terminated.png) |
| EBS 볼륨 삭제 | `check-leftovers.sh` 프로젝트 볼륨 0, 연결 안 된 볼륨 0 | 둘 다 0 (`vol-0c3df9448c49869d0`) |
| Elastic IP 반납 | `check-leftovers.sh` Elastic IP 0 | 계정 전체 0 (`52.78.251.4` 반납) |
| 인터넷 게이트웨이 삭제 | `check-leftovers.sh` 0 | 0 |
| VPC, 서브넷, 라우팅 테이블, 보안 그룹 삭제 | `check-leftovers.sh` 모두 0 | 모두 0, 키 페어와 NAT 게이트웨이도 0. [`evidence/prod-leftovers.txt`](evidence/prod-leftovers.txt) |
| DNS 레코드 | 다시 만들었으므로 지우지 않고 새 IP로 고침 | `mcp`, `auth` 모두 `54.116.149.126` (가비아 권한 서버 조회) |
| 결제 콘솔 | 다음 날 옛 자원의 과금 항목이 늘지 않음 (스크린샷) | 확인 전 |
