# Public ALB (Build Guide Phase 9, Step 49). The only public tier; HTTPS :443 -> api target group :8000.
# JWT verification happens in FastAPI (ALB-only is enough and cheaper). AUTHORED + VALIDATED ONLY.

variable "project" { type = string }
variable "vpc_id" { type = string }
variable "public_subnet_ids" { type = list(string) }
variable "alb_security_group_id" { type = string }
variable "retire_http_forward" {
  type    = bool
  default = false # TLS cutover phase (d): flip ONLY after CloudFront talks https to the ALB (RUNBOOK)
}

variable "certificate_arn" {
  type    = string
  default = "" # set to the ACM cert ARN before apply
}
variable "origin_verify_secret" {
  type      = string
  default   = "" # Sec/P0: the X-Origin-Verify value CloudFront stamps (must match api_cdn's)
  sensitive = true
}
variable "enforce_origin_verify" {
  type    = bool
  default = false # Sec/P0 phase 2: flip ONLY after the distro is Deployed with the header, or the edge 403s
}

# Access logs (TODO Sec/P2): request-level audit for the only public tier. ALB log delivery
# supports SSE-S3 only; us-east-1 delivery comes from the ELB account 127311923021.
resource "aws_s3_bucket" "access_logs" {
  bucket        = "${var.project}-alb-logs-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket                  = aws_s3_bucket.access_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    id     = "expire-90d"
    status = "Enabled"
    filter {}
    expiration { days = 90 }
  }
}

resource "aws_s3_bucket_policy" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::127311923021:root" }
      Action    = "s3:PutObject"
      Resource  = "${aws_s3_bucket.access_logs.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
    }]
  })
}

resource "aws_lb" "this" {
  name               = "${var.project}-alb"
  load_balancer_type = "application"
  internal           = false
  subnets            = var.public_subnet_ids
  security_groups    = [var.alb_security_group_id]

  access_logs {
    bucket  = aws_s3_bucket.access_logs.id
    enabled = true
  }

  depends_on = [aws_s3_bucket_policy.access_logs]
}

resource "aws_lb_target_group" "api" {
  name        = "${var.project}-api-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip" # Fargate awsvpc

  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

locals {
  has_cert = var.certificate_arn != ""
  # Both halves must be present: enforcing with an empty secret would 403 ALL traffic.
  # nonsensitive(): the bool reveals nothing about the secret, and without it the sensitivity
  # taints the dynamic default_action and re-marks the live listener (spurious plan diff).
  enforce_origin = var.enforce_origin_verify && nonsensitive(var.origin_verify_secret != "")
}

# With a cert: terminate TLS at the ALB (443 forward) + redirect 80 -> 443.
resource "aws_lb_listener" "https" {
  count             = local.has_cert ? 1 : 0
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  dynamic "default_action" {
    for_each = local.enforce_origin ? [] : [1]
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.api.arn
    }
  }

  dynamic "default_action" {
    for_each = local.enforce_origin ? [1] : []
    content {
      type = "fixed-response"
      fixed_response {
        content_type = "application/json"
        message_body = "{\"detail\":\"forbidden\"}"
        status_code  = "403"
      }
    }
  }
}

# The 443 twin of the origin_verify rule below — the X-Origin-Verify discipline carries to TLS.
resource "aws_lb_listener_rule" "origin_verify_https" {
  count        = (local.has_cert && local.enforce_origin) ? 1 : 0
  listener_arn = aws_lb_listener.https[0].arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    http_header {
      http_header_name = "X-Origin-Verify"
      values           = [var.origin_verify_secret]
    }
  }
}

# Port-80 collision guard: the redirect listener exists only AFTER http_forward retires —
# the RUNBOOK both-listeners transitional state keeps 80-forward alive while CloudFront still
# talks HTTP (CloudFront never follows origin redirects; an early redirect = outage).
resource "aws_lb_listener" "http_redirect" {
  count             = (local.has_cert && var.retire_http_forward) ? 1 : 0
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  # Destroy-before-create across the port-80 handover (review finding, confidence 95): without
  # this edge terraform may CreateListener the redirect while http_forward still owns :80 —
  # AWS rejects with DuplicateListener and the apply fails mid-flight on the live path.
  depends_on = [aws_lb_listener.http_forward]

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# No cert (no domain yet): forward HTTP:80 directly to the API. TLS is terminated upstream by Amplify,
# which proxies /api/* to this ALB over HTTP. Swap to the HTTPS listeners once a domain + ACM cert exist.
# Sec/P0 phase 2 (enforce_origin): the default becomes 403 and only requests carrying the
# X-Origin-Verify header our CloudFront stamps are forwarded (rule below) — exactly one
# default_action materializes. (Applies to the no-cert path only; the ACM/443 path gets its own
# rule when a domain lands.)
resource "aws_lb_listener" "http_forward" {
  count             = var.retire_http_forward ? 0 : 1
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = local.enforce_origin ? [] : [1]
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.api.arn
    }
  }

  dynamic "default_action" {
    for_each = local.enforce_origin ? [1] : []
    content {
      type = "fixed-response"
      fixed_response {
        content_type = "application/json"
        message_body = "{\"detail\":\"forbidden\"}"
        status_code  = "403"
      }
    }
  }
}

resource "aws_lb_listener_rule" "origin_verify" {
  count        = (!var.retire_http_forward && local.enforce_origin) ? 1 : 0
  listener_arn = aws_lb_listener.http_forward[0].arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    http_header {
      http_header_name = "X-Origin-Verify"
      values           = [var.origin_verify_secret]
    }
  }
}

output "alb_dns_name" { value = aws_lb.this.dns_name }
output "alb_zone_id" { value = aws_lb.this.zone_id }
output "target_group_arn" { value = aws_lb_target_group.api.arn }
output "arn_suffix" { value = aws_lb.this.arn_suffix }
