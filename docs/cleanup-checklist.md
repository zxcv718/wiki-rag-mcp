# 리소스 정리 체크리스트

실습에 만든 AWS 자원을 위에서부터 차례로 정리하고, 항목마다 근거를 남긴다. 아래 체크는 2026-10-28 19:52 KST에 배포 사용자 `wiki-rag-deployer`로 정리한 결과다. 정리 직후 같은 코드로 다시 만들었으므로, 지금 떠 있는 서버는 아래 ID와 다른 새 자원이다([다시 만들기](#운영-스택-다시-만들기-삭제-뒤)).

## 체크리스트 (2026-10-28 정리)

### 1. 지우기 전

- [x] **배포 사용자로 로그인했다.** 루트로 지우면 태그 조건이 걸리지 않아 다른 자원도 지울 수 있다.
  `aws sts get-caller-identity --profile wiki-rag` 결과 `user/wiki-rag-deployer`
- [x] **배포 사용자가 이 프로젝트 자원만 지울 수 있다.** `terraform destroy`가 부르는 삭제 호출 12개는 허용되고, 태그 없는 서울 리전 기본 VPC 삭제는 거부된다(`--dry-run`, 자원은 바꾸지 않음).
  근거: [`evidence/cleanup-dry-run.txt`](evidence/cleanup-dry-run.txt)
- [x] **지울 목록을 먼저 봤다.** 아래 2의 자원과 보안 그룹 규칙 4개, 라우팅 테이블 연결을 더해 13개다.
  `terraform -chdir=infra plan -destroy` 결과 `Plan: 0 to add, 0 to change, 13 to destroy.`
  근거: [`evidence/prod-plan-destroy.txt`](evidence/prod-plan-destroy.txt)

### 2. 자원 삭제

`terraform -chdir=infra destroy` 결과 `Destroy complete! Resources: 13 destroyed.` (37초). 근거: [`evidence/prod-destroy.txt`](evidence/prod-destroy.txt)

- [x] **EC2** `i-01edbaf39408d24ef` 종료(terminate). 중지만 하면 인스턴스 요금은 멈춰도 볼륨과 공인 IP 요금이 계속 붙는다.
  근거: 잔여 자원 점검 0, 콘솔에서 "종료됨" [`evidence/prod-ec2-terminated.png`](evidence/prod-ec2-terminated.png)
- [x] **EBS** 루트 볼륨 `vol-0c3df9448c49869d0`(20GB) 삭제. `DeleteOnTermination=true`라 인스턴스와 함께 지워진다. 연결이 끊긴 볼륨도 용량만큼 과금되므로 계정 전체의 연결 안 된 볼륨도 셌다.
  근거: 프로젝트 볼륨 0, 연결 안 된 볼륨 0
- [x] **Elastic IP** `eipalloc-0c18065934a830a35`(`52.78.251.4`) 연결 해제 뒤 반납(release). 공인 IPv4는 인스턴스에 붙어 있든 아니든 시간당 과금된다.
  근거: 계정 전체 Elastic IP 0
- [x] **인터넷 게이트웨이** `igw-023e2ba76f935ad15` VPC에서 분리 뒤 삭제. 자체 요금은 없지만 남으면 VPC를 지울 수 없다.
  근거: 0
- [x] **VPC** `vpc-0b900133c37418e1b` 마지막에 삭제. 안에 남은 것이 있으면 `DependencyViolation`으로 실패하므로, 성공했다면 안쪽이 모두 지워졌다는 뜻이다.
  근거: 0
- [x] **나머지 VPC 구성 요소** 서브넷 `subnet-05d8c7e84536436cd`, 라우팅 테이블 `rtb-031342c5cabae623b`(서브넷 연결 포함), 보안 그룹 `sg-0ab61932333ffdfc3`(규칙 4개 포함), 키 페어 `wiki-rag-operator` 삭제.
  근거: 모두 0
- [x] **만들지 않은 과금 자원이 정말 없다.** NAT 게이트웨이, 로드 밸런서(ELB/ALB), RDS는 Terraform 코드에 없고 만들지 않았다. NAT 게이트웨이는 계정 전체를 셌다.
  근거: NAT 게이트웨이 0

### 3. 지운 뒤 확인

- [x] **남은 자원이 없다.** 태그로 찾은 자원과, 태그와 상관없이 과금되는 것(연결 안 된 볼륨, 계정 전체 Elastic IP, NAT 게이트웨이)을 함께 센다.
  `AWS_PROFILE=wiki-rag infra/check-leftovers.sh` 근거: [`evidence/prod-leftovers.txt`](evidence/prod-leftovers.txt)

  ```
    0  EC2 (종료되지 않은 것)
    0  EBS 볼륨 (프로젝트)
    0  EBS 볼륨 (연결 안 됨, 전체)
    0  Elastic IP (전체)
    0  인터넷 게이트웨이
    0  VPC
    0  서브넷
    0  라우팅 테이블
    0  보안 그룹
    0  키 페어
    0  NAT 게이트웨이 (전체)
  ```

- [x] **반납한 IP를 가리키는 DNS가 없다.** 반납한 Elastic IP는 다른 AWS 고객에게 갈 수 있어, 레코드가 남으면 내 도메인이 남의 서버를 가리킨다. 바로 다시 만들었으므로 가비아 `mcp`, `auth` A 레코드를 지우지 않고 새 IP `54.116.149.126`으로 고쳤다(가비아 권한 서버 조회). `www` CNAME(Vercel)은 이 과제와 무관하다.
- [x] **예전 평가용 CNAME을 지웠다.** 가비아 CNAME `b3-1`(`b3-1.duckdns.org`를 가리킴)을 DuckDNS 이름보다 먼저 지운다. 반대로 지우면 CNAME이 남아 있는 동안 남이 그 DuckDNS 이름을 가져가 `b3-1.dmssh.store`로 온 요청을 받을 수 있다.
  근거: 2026-10-28 20:41 KST 가비아 권한 서버 조회 `dig b3-1.dmssh.store @ns.gabia.net` 결과 `NXDOMAIN`
- [ ] **DuckDNS 이름 `b3-1`을 지운다.** CNAME을 지운 뒤라 이제 지워도 된다. 가리키는 값은 이미 비어 있다.
- [x] **과금 위험을 확인했다.** 결제 화면 스크린샷(명세의 선택 항목)은 찍지 않았다. 결제 화면은 루트 계정이나 결제 권한을 받은 IAM 사용자만 볼 수 있는데, 과제 명세가 루트 계정 사용을 금지하고 배포 사용자는 최소 권한이라 결제 권한이 없다. 대신 위 잔여 자원 점검의 과금 기준 항목이 모두 0인 것을 확인했고, 뜻밖의 비용은 예산 경보 `wiki-rag-monthly`가 알린다.

## 참고

### 추적 기준

- **모든 자원을 Terraform으로 만든다.** 콘솔에서 손으로 만든 자원이 없어야 `terraform destroy` 한 번으로 빠짐없이 지워진다. 무엇을 만들었는지는 `infra/main.tf`와 `terraform output resource_ids`가 목록이다.
- **모든 자원에 `project=wiki-rag-mcp` 태그를 붙인다.** Terraform 공급자의 `default_tags`로 붙이므로 빠뜨릴 수 없다. 루트 볼륨, Elastic IP, 키 페어까지 태그가 붙은 것을 확인했다.
- **지우기 권한도 이 태그에 묶는다.** 배포 사용자는 이 태그가 붙은 자원만 지울 수 있어서, 정리하다가 다른 자원(예: 서울 리전 기본 VPC)을 지울 수 없다(`infra/iam/deployer-policy.json`).
- **정리가 끝났는지는 태그와 과금 기준 두 가지로 본다.** `infra/check-leftovers.sh`가 태그로 찾은 자원과, 태그와 상관없이 과금되는 것을 함께 센다. 조회가 실패하면 0으로 찍지 않고 멈춘다.

### 삭제 순서와 이유

`terraform destroy`는 자원 사이의 의존 관계를 거꾸로 따라 지운다. 손으로 지울 때도 같은 순서여야 한다.

1. **Elastic IP 연결 해제와 반납.** 공인 IP가 매핑된 채로는 인터넷 게이트웨이를 분리할 수 없다.
2. **EC2 종료.** 루트 볼륨이 함께 지워진다. 인스턴스의 네트워크 인터페이스가 없어져야 서브넷과 보안 그룹을 지울 수 있다.
3. **보안 그룹 규칙과 보안 그룹, 라우팅 테이블 연결과 라우팅 테이블 삭제.**
4. **인터넷 게이트웨이 분리와 삭제, 서브넷 삭제.**
5. **VPC 삭제.** 이 단계가 성공하면 안쪽이 모두 지워졌다는 뜻이다.

### 운영 스택 다시 만들기 (삭제 뒤)

지운 뒤 서버가 다시 필요하면 같은 코드로 새로 만든다. 인프라와 배포는 [검색 서버 설명의 "운영 스택 배포"](project-overview.md#운영-스택-배포) 1~6단계를 따른다. 지운 뒤라서 달라지는 점과 더 할 일은 아래와 같다. 2026-10-28 정리 직후 이 절차로 다시 만들었다(배포 208초, 시드 문서 200개, 색인 200개).

| 대상 | 지운 뒤 | 할 일 |
|---|---|---|
| Elastic IP | 반납해서 `52.78.251.4`는 돌아오지 않는다. 새 IP를 받는다 | 가비아의 `mcp`, `auth` A 레코드 값을 새 IP로 고친다. 레코드를 새로 만들 필요는 없다. Caddy가 인증서를 받으려면 이 이름이 새 서버로 와야 하므로 `push.sh` 전에 고친다 |
| 서버 비밀 (`deploy/.env`, `deploy/secrets/`) | 서버에만 있었으므로 함께 사라진다 | `push.sh`가 부르는 `init-secrets.sh`가 새로 만든다. 예전 토큰은 모두 무효라서 Claude Code 커넥터와 데모 에이전트는 다시 로그인한다. 데모 비밀번호도 바뀌므로 서버의 `deploy/.env`에서 확인한다 |
| 위키 DB | 백업이 없어 사라진다 | 가상 위키를 다시 옮긴다(아래 1). `membership.sh`로 바꾼 그룹 멤버십은 원래 값으로 돌아간다 |
| 검색 인덱스 | 사라진다 | 시드가 문서마다 이벤트를 내고, 워커가 처음부터 다시 색인한다. 2 vCPU에서 20분 안팎 걸린다 |
| HTTPS 인증서 | Caddy 볼륨과 함께 사라진다 | Caddy가 처음 뜰 때 새로 받는다. 같은 이름으로 7일에 5장까지 받을 수 있다 |
| SSH 허용 IP | `infra/terraform.tfvars`에 있는 값을 쓴다 | 지금 IP와 다르면 고친 뒤 apply한다 |

지운 직후 바로 다시 만들 때는 A 레코드를 지우지 않고, 새 IP가 나오면 값만 고쳐도 된다. 다만 그 사이에는 레코드가 반납한 IP를 가리키므로, 지우기와 다시 만들기 사이를 비워 두지 않는다.

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

### 운영을 끝낼 때 더 할 일

다시 만들지 않고 운영을 끝낼 때는 위 체크리스트에 더해 아래를 한다.

- 가비아 `mcp`, `auth` A 레코드를 지우고 `dig +short A mcp.dmssh.store`가 빈 값인지 본다.
- [README](../README.md)의 "실행과 검증" 표와 [검색 서버 설명](project-overview.md)을 운영이 끝난 상태로 고친다. 반납한 IP와 도메인은 운영 당시 주소로만 남기고 접속 링크는 없앤다. `docs/architecture.html`을 지울 때는 README의 링크도 함께 지운다.

### 정리하지 않는 것

| 대상 | 이유 |
|---|---|
| 서울 리전 기본 VPC | AWS가 계정마다 만들어 둔 것이고 과금이 없다. 배포 사용자 권한으로는 지울 수도 없다(`evidence/cleanup-dry-run.txt`) |
| IAM 사용자 `wiki-rag-deployer` | 과금이 없고 같은 인프라를 다시 만들 때 쓴다. 프로젝트를 끝낼 때 콘솔 접근과 CLI 로그인 권한을 없앤다 |
| 예산 경보 `wiki-rag-monthly` | 과금이 없고, 정리 뒤에 뜻밖의 비용이 생기면 알려 준다 |

### 이전 연습 기록

2026-10-07에는 평가 때마다 작은 서버를 따로 만들고 지우는 방식을 두 번 연습했다(13개 삭제, 잔여 자원 모두 0, [`evidence/b3-1-rehearsal.txt`](evidence/b3-1-rehearsal.txt)). 과제 명세대로 실습 자원 전체를 정리하기로 하면서 그 방식과 코드(`infra/b3-1.sh`, DuckDNS 갱신)는 지웠다.
