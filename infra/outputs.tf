output "public_ip" {
  description = "Elastic IP. 도메인 A 레코드가 가리킬 주소"
  value       = aws_eip.server.public_ip
}

output "ssh" {
  description = "서버 접속 명령"
  value       = "ssh -i ~/.ssh/wiki-rag-m5.pem ubuntu@${aws_eip.server.public_ip}"
}

output "health_url" {
  description = "외부 접속 확인 주소 (과제 검증 방식 B)"
  value       = "http://${aws_eip.server.public_ip}/health"
}

output "resource_ids" {
  description = "정리 체크리스트에서 삭제를 확인할 자원들"
  value = {
    vpc              = aws_vpc.main.id
    subnet           = aws_subnet.public.id
    internet_gateway = aws_internet_gateway.main.id
    route_table      = aws_route_table.public.id
    security_group   = aws_security_group.server.id
    instance         = aws_instance.server.id
    root_volume      = aws_instance.server.root_block_device[0].volume_id
    elastic_ip       = aws_eip.server.allocation_id
    key_pair         = aws_key_pair.operator.key_name
  }
}
