# Route53 zone + ACM cert for the real domain (TODO P1: TLS at the ALB, retire api_cdn).
# Zone + cert + validation records are pure adds and safe pre-delegation; the cert stays
# PENDING_VALIDATION until the registrar (Squarespace) nameservers point at this zone.
# aws_acm_certificate_validation (which BLOCKS until issuance) is gated on var.delegated.

variable "domain_name" { type = string }
variable "delegated" {
  type    = bool
  default = false # flip AFTER the registrar NS records point at this zone
}

resource "aws_route53_zone" "this" {
  name = var.domain_name
}

resource "aws_acm_certificate" "this" {
  domain_name               = var.domain_name
  subject_alternative_names = ["*.${var.domain_name}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "validation" {
  for_each = {
    for dvo in aws_acm_certificate.this.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }
  zone_id         = aws_route53_zone.this.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 300
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "this" {
  count                   = var.delegated ? 1 : 0
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [for r in aws_route53_record.validation : r.fqdn]
}

output "zone_id" { value = aws_route53_zone.this.zone_id }
output "nameservers" { value = aws_route53_zone.this.name_servers }
output "certificate_arn" { value = aws_acm_certificate.this.arn }
