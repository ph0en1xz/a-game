output "vpc_id" {
  description = "VPC Id"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs, one per AZ"
  value       = aws_subnet.public_subnets[*].id
}

output "private_subnet_ids" {
  description = "Private subnets IDs, one per AZ"
  value       = aws_subnet.private_subnets[*].id
}