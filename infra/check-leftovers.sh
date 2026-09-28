#!/usr/bin/env bash
# 서울 리전에 과금될 수 있는 이 프로젝트 자원이 남았는지 본다 (docs/cleanup-checklist.md).
#
#   AWS_PROFILE=wiki-rag infra/check-leftovers.sh
#
# 태그로 찾은 자원과, 태그와 상관없이 과금되는 것(연결 안 된 볼륨, 할당된 Elastic IP)을 함께 본다.
# 정리가 끝났으면 모든 줄이 0이다.
set -euo pipefail
export AWS_REGION="${AWS_REGION:-ap-northeast-2}"
tag=(--filters Name=tag:project,Values=wiki-rag-mcp)

count() { printf '%3s  %s\n' "$(aws ec2 "${@:2}" --output text | wc -w | tr -d ' ')" "$1"; }

count "EC2 (종료되지 않은 것)" describe-instances "${tag[@]}" Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].InstanceId'
count "EBS 볼륨 (프로젝트)" describe-volumes "${tag[@]}" --query 'Volumes[].VolumeId'
count "EBS 볼륨 (연결 안 됨, 전체)" describe-volumes --filters Name=status,Values=available --query 'Volumes[].VolumeId'
count "Elastic IP (전체)" describe-addresses --query 'Addresses[].AllocationId'
count "인터넷 게이트웨이" describe-internet-gateways "${tag[@]}" --query 'InternetGateways[].InternetGatewayId'
count "VPC" describe-vpcs "${tag[@]}" --query 'Vpcs[].VpcId'
count "서브넷" describe-subnets "${tag[@]}" --query 'Subnets[].SubnetId'
count "라우팅 테이블" describe-route-tables "${tag[@]}" --query 'RouteTables[].RouteTableId'
count "보안 그룹" describe-security-groups "${tag[@]}" --query 'SecurityGroups[].GroupId'
count "키 페어" describe-key-pairs "${tag[@]}" --query 'KeyPairs[].KeyName'
count "NAT 게이트웨이 (전체)" describe-nat-gateways --filter Name=state,Values=pending,available --query 'NatGateways[].NatGatewayId'
