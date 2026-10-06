# wiki-rag-mcp 배포 인프라 (설계서 ADR-23). 서울 리전에 VPC 하나, 퍼블릭 서브넷 하나, EC2 한 대를 둔다.
#
# 외부 요청은 인터넷 게이트웨이를 지나 퍼블릭 서브넷으로 들어오고(서브넷 라우팅의 0.0.0.0/0 경로가 게이트웨이를
# 가리킴), 보안 그룹(80·443은 전체, 22는 운영자 IP만)을 통과해 EC2에 닿는다.
#
# 같은 코드로 스택 두 개를 만든다. default workspace는 운영 서버, b3-1 workspace는 과제 B3-1 평가 때마다
# 띄웠다가 평가자 앞에서 지우는 작은 서버다(infra/b3-1.sh). 운영 스택의 이름과 태그는 그대로 두고, b3-1 스택에만
# 이름 접미사와 stack=b3-1 태그를 붙인다.
#
# 모든 자원에 project=wiki-rag-mcp 태그를 붙인다. 배포용 IAM 사용자는 이 태그가 있는 자원만 지울 수 있고
# (infra/iam/deployer-policy.json), 정리할 때도 이 태그로 남은 자원을 찾는다(docs/cleanup-checklist.md).

terraform {
  required_version = ">= 1.16"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
}

locals {
  stack  = terraform.workspace == "default" ? "prod" : terraform.workspace
  b3_1   = local.stack == "b3-1"
  suffix = local.b3_1 ? "-b3-1" : ""
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile
  default_tags {
    tags = merge(
      { project = "wiki-rag-mcp", managed-by = "terraform" },
      local.b3_1 ? { stack = "b3-1" } : {},
    )
  }
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  tags                 = { Name = "wiki-rag-vpc${local.suffix}" }

  lifecycle {
    precondition {
      condition     = contains(["prod", "b3-1"], local.stack)
      error_message = "workspace는 default(운영) 또는 b3-1(과제 평가)만 쓴다."
    }
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "wiki-rag-igw${local.suffix}" }
}

# 공인 IP는 서브넷에서 자동으로 주지 않고 Elastic IP로 붙인다. 도메인 A 레코드가 가리킬 주소가 재시작해도 바뀌지 않게
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = false
  tags                    = { Name = "wiki-rag-public${local.suffix}" }
}

# 이 경로가 있어야 서브넷이 "퍼블릭"이 된다. 없으면 공인 IP가 있어도 밖으로 나가는 길이 없다
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "wiki-rag-public-rt${local.suffix}" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "server" {
  name        = "wiki-rag-server${local.suffix}"
  description = "HTTP and HTTPS from anywhere, SSH from the operator IP only"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "wiki-rag-server${local.suffix}" }
}

# HTTP는 Let's Encrypt 인증서 발급(HTTP-01)과 HTTPS로 넘기는 데 쓴다
resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.server.id
  description       = "HTTP"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.server.id
  description       = "HTTPS"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.server.id
  description       = "SSH from the operator only"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.ssh_cidr
}

# 패키지 설치, 코드와 모델 내려받기, 인증서 발급에 밖으로 나가는 연결이 필요하다
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.server.id
  description       = "Outbound to the internet"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_key_pair" "operator" {
  key_name   = "wiki-rag-operator${local.suffix}"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_instance" "server" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = local.b3_1 ? "t3.micro" : var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.server.id]
  key_name               = aws_key_pair.operator.key_name
  user_data = local.b3_1 ? templatefile("${path.module}/cloud-init-b3-1.yaml.tftpl", {
    domain            = var.b3_1_domain
    duckdns_subdomain = var.duckdns_subdomain
    duckdns_token     = var.duckdns_token
    acme_staging      = var.acme_staging
    index_html        = file("${path.module}/../deploy/site/index.html")
  }) : file("${path.module}/cloud-init.yaml")

  # 인스턴스 메타데이터는 토큰(IMDSv2)으로만 읽게 한다. 서버 안의 SSRF로 자격 증명을 빼내는 경로를 막는다
  # b3-1 스택은 user_data에 DuckDNS 토큰이 있어, 컨테이너(홉 하나 더)에서는 메타데이터를 읽지 못하게 한다.
  # 운영 스택은 null로 두어 지금 설정을 바꾸지 않는다
  metadata_options {
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = local.b3_1 ? 1 : null
  }

  root_block_device {
    volume_size           = local.b3_1 ? 8 : var.root_volume_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
    tags                  = { Name = "wiki-rag-root${local.suffix}" }
  }

  tags = { Name = "wiki-rag-server${local.suffix}" }

  # 새 Ubuntu 이미지가 나올 때마다 서버를 다시 만들지 않는다
  lifecycle {
    ignore_changes = [ami, user_data]

    precondition {
      condition     = !local.b3_1 || (var.duckdns_subdomain != "" && var.duckdns_token != "")
      error_message = "b3-1 스택은 DuckDNS 이름과 토큰이 필요하다. infra/b3-1.sh로 실행한다."
    }
  }
}

resource "aws_eip" "server" {
  domain   = "vpc"
  instance = aws_instance.server.id
  tags     = { Name = "wiki-rag-eip${local.suffix}" }

  depends_on = [aws_internet_gateway.main]
}
