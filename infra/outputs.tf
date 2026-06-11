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
  description = "alb / api / cube / db / redis security groups (REQ-012 item 7: cube has its own SG)."
  value = {
    alb   = module.security.sg_alb
    api   = module.security.sg_api
    cube  = module.security.sg_cube
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

# TODO P3 148: deterministic live endpoints — outputs + SSM mirror for web builds/scripts.
output "api_edge_domain" { value = module.api_cdn.domain }

resource "aws_ssm_parameter" "api_edge_domain" {
  name  = "/uplift/live/api-edge-domain"
  type  = "String"
  value = module.api_cdn.domain
}

resource "aws_ssm_parameter" "alb_dns" {
  name  = "/uplift/live/alb-dns"
  type  = "String"
  value = module.alb.alb_dns_name
}

resource "aws_ssm_parameter" "cube_endpoint" {
  name  = "/uplift/live/cube-endpoint"
  type  = "String"
  value = "http://cube.uplift.local:4000"
}
