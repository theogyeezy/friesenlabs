# CloudFront edge for the API: serves HTTPS (trusted *.cloudfront.net cert, no domain needed) and
# origins to the internal-facing ALB over HTTP. This lets the Amplify site proxy /api/* to a valid
# HTTPS endpoint (Amplify rejects HTTP proxy targets) without owning a domain/cert. Swap for a real
# domain + ACM on the ALB later.

variable "project" { type = string }
variable "alb_dns" { type = string }

# Don't cache the API; forward method/query/headers/body through to the origin.
data "aws_cloudfront_cache_policy" "disabled" { name = "Managed-CachingDisabled" }
data "aws_cloudfront_origin_request_policy" "all_viewer" { name = "Managed-AllViewerExceptHostHeader" }

resource "aws_cloudfront_distribution" "api" {
  enabled         = true
  comment         = "${var.project} API edge (HTTPS -> ALB HTTP)"
  http_version    = "http2"
  is_ipv6_enabled = true

  origin {
    domain_name = var.alb_dns
    origin_id   = "alb"
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" # ALB has no TLS cert; CloudFront terminates TLS at the edge
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id         = "alb"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

output "domain" { value = aws_cloudfront_distribution.api.domain_name }
