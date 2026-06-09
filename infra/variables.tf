variable "aws_region" {
  description = "AWS region. us-east-1 or us-west-2 (Titan embeddings + us-east-1 billing alarms)."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project/name prefix for all resources."
  type        = string
  default     = "uplift"
}

variable "vpc_cidr" {
  description = "VPC CIDR. /16 split into two /20 public + two /20 private subnets."
  type        = string
  default     = "10.0.0.0/16"
}

variable "azs" {
  description = "Two availability zones (suffixes appended to region)."
  type        = list(string)
  default     = ["a", "b"]
}

variable "ecr_repos" {
  description = "ECR repositories to create (one image home per service)."
  type        = list(string)
  default     = ["api", "cube", "worker"]
}

variable "notify_email" {
  description = "Email for budget + alarm SNS notifications. Empty = no subscription wired (validate-clean)."
  type        = string
  default     = ""
}

variable "budgets_action_execution_role_arn" {
  description = "IAM role AWS Budgets assumes to apply the Deny-at-90% policy. Empty = budget action not created (BLOCKED: needs Nick)."
  type        = string
  default     = ""
}
