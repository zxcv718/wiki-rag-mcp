variable "region" {
  description = "모든 자원을 두는 리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "availability_zone" {
  description = "퍼블릭 서브넷과 서버를 둘 가용 영역. m7i-flex.large는 서울 네 영역 모두에 있다"
  type        = string
  default     = "ap-northeast-2a"
}

variable "aws_profile" {
  description = "Terraform을 실행할 AWS CLI 프로필. 루트가 아닌 최소 권한 IAM 사용자(wiki-rag-deployer)"
  type        = string
  default     = "wiki-rag"
}

variable "ssh_cidr" {
  description = "SSH를 허용할 운영자 IP. 예: 203.0.113.7/32. 기본값을 두지 않아 매번 명시하게 한다"
  type        = string

  validation {
    condition     = can(cidrhost(var.ssh_cidr, 0)) && var.ssh_cidr != "0.0.0.0/0"
    error_message = "ssh_cidr는 CIDR 형식이어야 하고 0.0.0.0/0일 수 없다."
  }
}

variable "ssh_public_key_path" {
  description = "서버에 등록할 SSH 공개 키"
  type        = string
  default     = "~/.ssh/wiki-rag-m5.pub"
}

variable "instance_type" {
  description = "무료 플랜 대상 중 메모리가 가장 큰 유형. 임베딩 모델을 올린 프로세스 두 개와 DB들이 5~6GB를 쓴다"
  type        = string
  default     = "m7i-flex.large"
}

variable "root_volume_gb" {
  description = "루트 디스크 크기. 모델 파일 2.3GB, torch, Docker 이미지와 DB 데이터를 담는다"
  type        = number
  default     = 20
}

# 아래는 b3-1 스택(과제 평가용)에서만 쓴다. infra/b3-1.sh가 넘긴다

variable "b3_1_domain" {
  description = "b3-1 스택의 HTTPS 주소. 가비아에 DuckDNS 이름을 가리키는 CNAME을 한 번 넣어 둔다"
  type        = string
  default     = "b3-1.dmssh.store"
}

variable "duckdns_subdomain" {
  description = "DuckDNS 이름(<이름>.duckdns.org의 <이름>). 서버가 켜질 때 자기 IP를 여기에 등록한다"
  type        = string
  default     = ""
}

variable "duckdns_token" {
  description = "DuckDNS 토큰. 저장소 밖 ~/.config/wiki-rag/duckdns.env에서 읽는다"
  type        = string
  default     = ""
  sensitive   = true
}

variable "acme_staging" {
  description = "true면 Let's Encrypt 시험 서버에서 인증서를 받는다. 리허설에서 실제 발급 한도(같은 이름 7일 5장)를 쓰지 않으려고"
  type        = bool
  default     = false
}
