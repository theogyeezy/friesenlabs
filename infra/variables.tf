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
