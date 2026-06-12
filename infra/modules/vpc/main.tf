# VPC across two AZs: 2 public subnets (ALB + NAT) + 2 private subnets (Aurora, ECS, Redis, worker).
# Build Guide §Step 6.

variable "project" { type = string }
variable "vpc_cidr" { type = string }
variable "azs" { type = list(string) }
variable "region" { type = string }
variable "log_retention_days" {
  type    = number
  default = 30 # one knob for every uplift log group (TODO Sec/P3 213)
}

locals {
  # /16 → four /20s. Public: 10.0.0.0/20, 10.0.16.0/20. Private: 10.0.128.0/20, 10.0.144.0/20.
  public_cidrs  = [cidrsubnet(var.vpc_cidr, 4, 0), cidrsubnet(var.vpc_cidr, 4, 1)]
  private_cidrs = [cidrsubnet(var.vpc_cidr, 4, 8), cidrsubnet(var.vpc_cidr, 4, 9)]
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "${var.project}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.project}-igw" }
}

resource "aws_subnet" "public" {
  count                   = length(var.azs)
  vpc_id                  = aws_vpc.this.id
  cidr_block              = local.public_cidrs[count.index]
  availability_zone       = "${var.region}${var.azs[count.index]}"
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project}-public-${var.azs[count.index]}", Tier = "public" }
}

resource "aws_subnet" "private" {
  count             = length(var.azs)
  vpc_id            = aws_vpc.this.id
  cidr_block        = local.private_cidrs[count.index]
  availability_zone = "${var.region}${var.azs[count.index]}"
  tags              = { Name = "${var.project}-private-${var.azs[count.index]}", Tier = "private" }
}

# --- Public routing → IGW ---
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = { Name = "${var.project}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --- One NAT gateway in the first public subnet (private tasks reach Bedrock + api.anthropic.com) ---
# COST LEVER (~$32/mo + data): trim with VPC endpoints (S3 gateway free; interface endpoints for
# bedrock-runtime, ecr.api, ecr.dkr, logs, secretsmanager). Worker still needs egress to anthropic.
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${var.project}-nat-eip" }
}

resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = "${var.project}-nat" }
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this.id
  }
  tags = { Name = "${var.project}-private-rt" }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# --- Free S3 gateway endpoint (trim NAT data costs) ---
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  tags              = { Name = "${var.project}-s3-endpoint" }
}

# --- VPC flow logs (Sec, REQ-012 item 5): traffic-level audit for the VPC holding tenant data.
# ALL traffic (accept + reject) into CloudWatch; the reject stream is the lateral-movement /
# exfil-attempt signal GuardDuty and humans read. Unconditional + additive.
resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/vpc/${var.project}-flow-logs"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "flow_logs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  name               = "${var.project}-vpc-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume.json
}

resource "aws_iam_role_policy" "flow_logs" {
  name = "flow-log-delivery"
  role = aws_iam_role.flow_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
      ]
      Resource = [
        aws_cloudwatch_log_group.flow_logs.arn,
        "${aws_cloudwatch_log_group.flow_logs.arn}:*",
      ]
    }]
  })
}

resource "aws_flow_log" "vpc" {
  vpc_id               = aws_vpc.this.id
  traffic_type         = "ALL"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.flow_logs.arn
  iam_role_arn         = aws_iam_role.flow_logs.arn
  tags                 = { Name = "${var.project}-vpc-flow-logs" }
}

output "vpc_id" { value = aws_vpc.this.id }
output "public_subnet_ids" { value = aws_subnet.public[*].id }
output "private_subnet_ids" { value = aws_subnet.private[*].id }

# Cloud Map private DNS namespace (uplift.local) — internal service discovery so api/worker can
# resolve cube (and future services) by name instead of chasing task IPs.
resource "aws_service_discovery_private_dns_namespace" "internal" {
  name = "uplift.local"
  vpc  = aws_vpc.this.id
}

output "service_discovery_namespace_id" { value = aws_service_discovery_private_dns_namespace.internal.id }
