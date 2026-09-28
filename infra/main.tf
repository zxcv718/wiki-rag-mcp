# wiki-rag-mcp 배포 인프라 (설계서 ADR-23). 서울 리전에 VPC 하나, 퍼블릭 서브넷 하나, EC2 한 대를 둔다.
#
# 외부 요청은 인터넷 게이트웨이를 지나 퍼블릭 서브넷으로 들어오고(서브넷 라우팅의 0.0.0.0/0 경로가 게이트웨이를
# 가리킴), 보안 그룹(80·443은 전체, 22는 운영자 IP만)을 통과해 EC2에 닿는다.
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

provider "aws" {
  region  = var.region
  profile = var.aws_profile
  default_tags {
    tags = {
      project    = "wiki-rag-mcp"
      managed-by = "terraform"
    }
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
  tags                 = { Name = "wiki-rag-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "wiki-rag-igw" }
}

# 공인 IP는 서브넷에서 자동으로 주지 않고 Elastic IP로 붙인다. 도메인 A 레코드가 가리킬 주소가 재시작해도 바뀌지 않게
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = false
  tags                    = { Name = "wiki-rag-public" }
}

# 이 경로가 있어야 서브넷이 "퍼블릭"이 된다. 없으면 공인 IP가 있어도 밖으로 나가는 길이 없다
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "wiki-rag-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "server" {
  name        = "wiki-rag-server"
  description = "HTTP and HTTPS from anywhere, SSH from the operator IP only"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "wiki-rag-server" }
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
  key_name   = "wiki-rag-operator"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_instance" "server" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.server.id]
  key_name               = aws_key_pair.operator.key_name
  user_data              = file("${path.module}/cloud-init.yaml")

  # 인스턴스 메타데이터는 토큰(IMDSv2)으로만 읽게 한다. 서버 안의 SSRF로 자격 증명을 빼내는 경로를 막는다
  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
    tags                  = { Name = "wiki-rag-root" }
  }

  tags = { Name = "wiki-rag-server" }

  # 새 Ubuntu 이미지가 나올 때마다 서버를 다시 만들지 않는다
  lifecycle {
    ignore_changes = [ami, user_data]
  }
}

resource "aws_eip" "server" {
  domain   = "vpc"
  instance = aws_instance.server.id
  tags     = { Name = "wiki-rag-eip" }

  depends_on = [aws_internet_gateway.main]
}
