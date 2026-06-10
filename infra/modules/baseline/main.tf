# Account baseline (Build Guide §Step 5): never build as root; turn on the account guardrails.
# - account-level S3 block-public-access
# - an org/multi-region CloudTrail with its own encrypted, access-blocked log bucket
# AWS Config recorder + an SCP denying CloudTrail/Config disablement are org-level and tracked
# as a follow-up (need Org context); see BUILD_STATUS.md.

variable "project" { type = string }

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# --- Account-wide S3 public access block ---
resource "aws_s3_account_public_access_block" "this" {
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- CloudTrail log bucket ---
resource "aws_s3_bucket" "trail" {
  bucket = "${var.project}-cloudtrail-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "trail" {
  bucket                  = aws_s3_bucket.trail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

data "aws_iam_policy_document" "trail_bucket" {
  statement {
    sid       = "AWSCloudTrailAclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.trail.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
  }
  statement {
    sid       = "AWSCloudTrailWrite"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.trail.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }
}

resource "aws_s3_bucket_policy" "trail" {
  bucket = aws_s3_bucket.trail.id
  policy = data.aws_iam_policy_document.trail_bucket.json
}

resource "aws_cloudtrail" "org" {
  name                          = "${var.project}-trail"
  s3_bucket_name                = aws_s3_bucket.trail.id
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true
  depends_on                    = [aws_s3_bucket_policy.trail]

  # Data events (TODO Sec/P2): object-level audit on OUR buckets + secret reads on uplift/* only —
  # scoped selectors, not account-wide (data events bill per event).
  advanced_event_selector {
    name = "uplift-s3-objects"
    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }
    field_selector {
      field       = "resources.ARN"
      starts_with = ["arn:aws:s3:::${var.project}-"]
    }
  }

  advanced_event_selector {
    name = "uplift-secrets-reads"
    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::SecretsManager::Secret"]
    }
    field_selector {
      field       = "resources.ARN"
      starts_with = ["arn:aws:secretsmanager:us-east-1:${data.aws_caller_identity.current.account_id}:secret:${var.project}/"]
    }
  }

  # Management events stay on (the default when any advanced selector is present must re-state it).
  advanced_event_selector {
    name = "management-events"
    field_selector {
      field  = "eventCategory"
      equals = ["Management"]
    }
  }
}

output "cloudtrail_bucket" { value = aws_s3_bucket.trail.id }
output "cloudtrail_name" { value = aws_cloudtrail.org.name }
