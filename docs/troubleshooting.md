# 트러블슈팅 보고서

배포하면서 실제로 겪은 문제 두 건입니다. 두 건 모두 설정을 먼저 바꾸지 않고, 가설마다 확인할 근거를 정해 하나씩 지운 뒤 조치했습니다. 원래 출력은 `docs/evidence/`에 있습니다.

## 1. CLI가 IAM 사용자가 아니라 루트 계정으로 로그인됨

| 항목 | 내용 |
|---|---|
| 증상 | 최소 권한 IAM 사용자(`wiki-rag-deployer`)로 쓰려고 만든 CLI 프로필 `wiki-rag`에 `aws login`을 했는데, 출력이 `Updated profile wiki-rag to use arn:aws:iam::488914879507:root credentials`로 끝났다. 과제 제약은 "루트 계정은 사용하지 않는다"이다. |
| 원인 가설 | ① 브라우저에 루트 콘솔 세션이 남아 있어 `aws login`이 그 세션을 그대로 가져갔다. ② IAM 사용자에게 CLI 로그인 권한이 없어 루트로 대신 넘어갔다. |
| 검증 방법 | `aws sts get-caller-identity --profile wiki-rag`로 실제 주체를 확인하니 `...:root`였다. `~/.aws/config`의 `[profile wiki-rag]`에 `login_session = arn:aws:iam::488914879507:root`가 저장돼 있었다. 가설 ②는 AWS CLI 문서로 확인했다. IAM 사용자는 `SignInLocalDevelopmentAccess` 관리형 정책이 있어야 `aws login`을 쓸 수 있는데, 이 정책은 로그인 20분쯤 전에 이미 붙여 두었다(`aws iam list-attached-user-policies`). 그래서 가설 ②를 버리고 ①로 좁혔다. |
| 조치 | 브라우저를 IAM 사용자로 로그인한 뒤 다시 `aws login`을 했다. 처음에는 "기존 루트 세션을 IAM 사용자로 덮어쓸까요 (y/n)"라는 질문에 입력이 없어 실패했다. 그래서 프로필에서 옛 `login_session` 줄을 지우고 다시 실행했다. |
| 결과 | 출력이 `...:user/wiki-rag-deployer credentials`로 바뀌었다. 같은 프로필로 서울 리전 EC2 조회는 되고, S3 목록, 버지니아 리전 EC2, IAM 사용자 목록은 모두 거부되는 것을 확인했다(`docs/evidence/iam-least-privilege.txt`). Terraform의 자원 13개도 이 사용자로 권한 오류 없이 만들었다. |
| 재발 방지 | 인프라 명령을 돌리기 전에 `aws sts get-caller-identity`로 주체가 `user/wiki-rag-deployer`인지 먼저 본다. 루트 로그인 화면(이메일 입력)이 아니라 IAM 사용자 로그인 주소(`https://<계정 ID>.signin.aws.amazon.com/console`)로 들어간다. Terraform은 `profile = "wiki-rag"`를 코드에 고정해(`infra/main.tf`) 기본(루트) 프로필로 돌지 않게 한다. |

## 2. 이 노트북에서만 80번 포트가 가끔 연결되지 않음

| 항목 | 내용 |
|---|---|
| 증상 | 배포 직후 노트북에서 `curl http://mcp.dmssh.store/health`와 `curl http://52.78.251.4/health`가 가끔 `000`(curl 오류 7, "Couldn't connect to server")으로 실패했다. 한 번은 "Network is down"이었다. 같은 때 `https://mcp.dmssh.store/health`(443)와 SSH(22)는 정상이었다. |
| 원인 가설 | ① 보안 그룹에 80번 인바운드 규칙이 없거나 틀렸다. ② Caddy 설정에서 80번 처리가 잘못돼(도메인 HTTPS 전환과 IP용 `:80` 블록의 충돌) 연결을 받지 않는다. ③ 서버 밖, 노트북 쪽 네트워크 문제다. |
| 검증 방법 | ① `describe-security-group-rules`로 80번 규칙이 `0.0.0.0/0`임을 확인했다. ② 서버 안에서 `curl http://localhost/health`는 200, 도메인 이름으로 보내면 308(HTTPS로 넘김)이었다. 서버가 자기 공인 IP(인터넷 게이트웨이와 보안 그룹을 거쳐 들어오는 길)로 보낸 요청도 200이었다. ③ 노트북에서 다른 사이트의 80번(example.com)은 200이었다. 실패는 2~13ms 만에 났는데, 서울 리전까지 왕복해 거절당했다고 보기에는 너무 짧다. 서버에서 `tcpdump`로 보니, 성공할 때 노트북에서 온 SYN이 인스턴스로 들어와 Caddy 컨테이너까지 넘어갔다. 마지막으로 check-host.net의 해외 점검 노드 5곳(독일, 이란, 몰도바, 튀르키예, 미국)에서 `http://mcp.dmssh.store/health`를 부르니 모두 308이 왔다. 나중에 다른 노드 4곳(프랑스, 일본, 스웨덴, 미국)에서 다시 불러도 같았다. |
| 조치 | 서버, 보안 그룹, Caddy 설정은 바꾸지 않았다. 원인이 서버 쪽이 아니었기 때문이다. 외부 접속 증빙 가운데 도메인의 HTTP 요청이 HTTPS로 넘어가는지는 노트북 대신 해외 점검 노드 결과로 남겼다. |
| 결과 | 원인은 노트북의 네트워크 인터페이스(`en8`) 쪽에서 연결을 수 ms 안에 끊는 문제로 좁혀졌다. 서버의 80번은 외부에서 정상이다(`docs/evidence/port80-intermittent.txt`, `docs/evidence/external-access.txt`). |
| 재발 방지 | 외부 접속 문제는 적어도 두 곳(내 PC와 외부 점검 노드 또는 서버 자신의 공인 IP)에서 확인한 뒤 판단한다. curl의 오류 번호와 실패까지 걸린 시간을 먼저 본다(수 ms 만의 실패는 로컬 쪽일 가능성이 크다). 보안 그룹을 넓히는 조치(예: 전체 포트 허용)는 근거 없이 하지 않는다. |

## 외부 접속이 안 될 때 점검 순서

1. **이름 해석**: `dig +short A mcp.dmssh.store`가 Elastic IP를 가리키는가
2. **라우팅**: 서브넷의 라우팅 테이블에 `0.0.0.0/0` 경로가 인터넷 게이트웨이를 가리키는가, 게이트웨이가 VPC에 붙어 있는가
3. **보안 그룹**: 필요한 포트(80·443)가 `0.0.0.0/0`에 열려 있고, 22는 운영자 IP만인가
4. **공인 IP**: 인스턴스에 Elastic IP가 붙어 있는가
5. **서버 프로세스**: 서버 안에서 `curl http://localhost/health`가 200인가, `docker ps`에 컨테이너가 Up인가, `docker compose logs caddy`에 오류가 없는가
6. **어디서 실패하는가**: 다른 네트워크(외부 점검 노드)에서도 실패하는지 본다
