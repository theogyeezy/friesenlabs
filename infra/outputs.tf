output "vpc_id" {
  description = "The VPC id."
  value       = module.vpc.vpc_id
}

output "public_subnet_ids" {
  value = module.vpc.public_subnet_ids
}

output "private_subnet_ids" {
  value = module.vpc.private_subnet_ids
}

output "security_group_ids" {
  description = "alb / api / db / redis security groups."
  value = {
    alb   = module.security.sg_alb
    api   = module.security.sg_api
    db    = module.security.sg_db
    redis = module.security.sg_redis
  }
}

output "ecr_repository_urls" {
  value = module.ecr.repository_urls
}
