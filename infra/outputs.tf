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

output "aurora_cluster_endpoint" {
  value = module.data.cluster_endpoint
}

output "redis_primary_endpoint" {
  value = module.redis.primary_endpoint
}

output "s3_bucket_names" {
  value = module.s3.bucket_names
}

output "ecs_cluster_name" {
  value = module.ecs.cluster_name
}

output "cube_service_name" {
  value = module.cube.service_name
}

output "cognito_user_pool_id" {
  value = module.auth.user_pool_id
}

output "alb_dns_name" {
  value = module.alb.alb_dns_name
}

output "api_service_name" {
  value = module.api_service.service_name
}

output "web_app_url" {
  description = "Live Amplify URL for the hosted web app (null until web hosting is enabled with a GitHub token)."
  value       = length(module.web_hosting) > 0 ? module.web_hosting[0].branch_url : null
}
