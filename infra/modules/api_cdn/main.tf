# CloudFront edge for the API: serves HTTPS (trusted *.cloudfront.net cert, no domain needed) and
# origins to the internal-facing ALB over HTTP. This lets the Amplify site proxy /api/* to a valid
# HTTPS endpoint (Amplify rejects HTTP proxy targets) without owning a domain/cert. Swap for a real
# domain + ACM on the ALB later.

variable "project" { type = string }
variable "alb_dns" { type = string }
variable "api_origin_domain" {
  type    = string
  default = "" # set to api.<domain> at TLS-cutover phase (b) — flips the origin to https-only
}

variable "origin_verify_secret" {
  type      = string
  default   = "" # Sec/P0 phase 1: when set, CloudFront stamps X-Origin-Verify on every origin request
  sensitive = true
}

# Don't cache the API; forward method/query/headers/body through to the origin.
data "aws_cloudfront_cache_policy" "disabled" { name = "Managed-CachingDisabled" }
data "aws_cloudfront_origin_request_policy" "all_viewer" { name = "Managed-AllViewerExceptHostHeader" }

data "aws_caller_identity" "current" {}

# WAFv2 (CLOUDFRONT scope → must live in us-east-1) — managed rule sets + a per-IP rate limit on
# the public multi-tenant API edge.
resource "aws_wafv2_web_acl" "api" {
  name        = "${var.project}-api-edge"
  scope       = "CLOUDFRONT"
  description = "Common + KnownBadInputs managed rules + rate limit for the API edge."
  default_action {
    allow {}
  }

  rule {
    name     = "common"
    priority = 1
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "common"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "bad-inputs"
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "bad_inputs"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "rate-limit"
    priority = 3
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 2000 # requests / 5-min / IP
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "rate_limit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project}-api-edge"
    sampled_requests_enabled   = true
  }
}

# Standard access logs → an encrypted, ACL-enabled bucket (CloudFront logging requires bucket ACLs).
resource "aws_s3_bucket" "cf_logs" {
  bucket        = "${var.project}-cf-logs-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
}
resource "aws_s3_bucket_ownership_controls" "cf_logs" {
  bucket = aws_s3_bucket.cf_logs.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}
resource "aws_s3_bucket_acl" "cf_logs" {
  depends_on = [aws_s3_bucket_ownership_controls.cf_logs]
  bucket     = aws_s3_bucket.cf_logs.id
  acl        = "private"
}
resource "aws_s3_bucket_server_side_encryption_configuration" "cf_logs" {
  bucket = aws_s3_bucket.cf_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_public_access_block" "cf_logs" {
  bucket                  = aws_s3_bucket.cf_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_lifecycle_configuration" "cf_logs" {
  bucket = aws_s3_bucket.cf_logs.id
  rule {
    id     = "expire-90d"
    status = "Enabled"
    filter {}
    expiration { days = 90 }
  }
}

# Response-headers policy: HSTS + the standard security header set at the edge.
resource "aws_cloudfront_response_headers_policy" "sec" {
  name = "${var.project}-sec-headers"
  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }
    content_type_options { override = true }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
  }
}

resource "aws_cloudfront_distribution" "api" {
  enabled         = true
  comment         = "${var.project} API edge (HTTPS -> ALB HTTP)"
  http_version    = "http2"
  is_ipv6_enabled = true
  price_class     = "PriceClass_100" # NA + EU (audience); lowers per-GB once traffic starts
  web_acl_id      = aws_wafv2_web_acl.api.arn

  logging_config {
    bucket = aws_s3_bucket.cf_logs.bucket_domain_name
    prefix = "cf/"
  }

  origin {
    # TLS cutover phase (b): when api_origin_domain is set (api.<domain>, covered by the ACM
    # wildcard), CloudFront talks https-only to the ALB through it; the raw ELB hostname would
    # fail cert validation, so https requires the named origin.
    domain_name = var.api_origin_domain != "" ? var.api_origin_domain : var.alb_dns
    origin_id   = "alb"
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = var.api_origin_domain != "" ? "https-only" : "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }

    # Sec/P0: prove to the ALB that traffic came through OUR distribution — the SG prefix-list
    # admits every CloudFront customer, so the ALB listener requires this header (phase 2).
    dynamic "custom_header" {
      for_each = nonsensitive(var.origin_verify_secret != "") ? [1] : []
      content {
        name  = "X-Origin-Verify"
        value = var.origin_verify_secret
      }
    }
  }

  default_cache_behavior {
    target_origin_id           = "alb"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.sec.id
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

output "domain" { value = aws_cloudfront_distribution.api.domain_name }
