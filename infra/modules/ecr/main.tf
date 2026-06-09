# ECR (Build Guide §Step 8): an image home per service. Scan on push; immutable tags.

variable "project" { type = string }
variable "repos" { type = list(string) }

resource "aws_ecr_repository" "this" {
  for_each             = toset(var.repos)
  name                 = "${var.project}-${each.key}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

output "repository_urls" {
  value = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}
