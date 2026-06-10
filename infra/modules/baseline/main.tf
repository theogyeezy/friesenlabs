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

  # NOTE: Secrets Manager has no CloudTrail DATA events — GetSecretValue is a MANAGEMENT event
  # and is already captured by this trail (PutEventSelectors rejects
  # resources.type=AWS::SecretsManager::Secret; verified live 2026-06-09). The TODO item's
  # "Secrets" half is therefore satisfied by the management selector below.

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

# GuardDuty (TODO Sec/P3 214): threat detection for the account holding tenant data. Cheap at
# this CloudTrail/VPC-flow volume (~$1-5/mo).
resource "aws_guardduty_detector" "this" {
  enable = true
}

# AWS Config recorder + delivery channel (TODO P3 147): account-level baseline (the SCP half
# still needs an AWS Org). Records resource configuration history into the trail bucket.
data "aws_iam_policy_document" "config_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "config" {
  name               = "${var.project}-config-recorder"
  assume_role_policy = data.aws_iam_policy_document.config_assume.json
}

resource "aws_iam_role_policy_attachment" "config" {
  role       = aws_iam_role.config.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWS_ConfigRole"
}

resource "aws_iam_role_policy" "config_s3" {
  name = "config-delivery"
  role = aws_iam_role.config.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["s3:PutObject"]
        Resource  = "${aws_s3_bucket.trail.arn}/config/AWSLogs/*"
        Condition = { StringEquals = { "s3:x-amz-acl" = "bucket-owner-full-control" } }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetBucketAcl"]
        Resource = aws_s3_bucket.trail.arn
      }
    ]
  })
}

resource "aws_config_configuration_recorder" "this" {
  name     = "${var.project}-recorder"
  role_arn = aws_iam_role.config.arn
  recording_group {
    all_supported                 = true
    include_global_resource_types = true
  }
}

resource "aws_config_delivery_channel" "this" {
  name           = "${var.project}-config"
  s3_bucket_name = aws_s3_bucket.trail.id
  s3_key_prefix  = "config"
  depends_on     = [aws_config_configuration_recorder.this]
}

resource "aws_config_configuration_recorder_status" "this" {
  name       = aws_config_configuration_recorder.this.name
  is_enabled = true
  depends_on = [aws_config_delivery_channel.this]
}
