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

# Lifecycle: keep recent tagged images for rollback; expire untagged build cruft fast.
resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 14 days"
        selection    = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 14 }
        action       = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the last 20 tagged images"
        selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
        action       = { type = "expire" }
      }
    ]
  })
}

output "repository_urls" {
  value = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}
