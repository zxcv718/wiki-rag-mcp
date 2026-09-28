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
