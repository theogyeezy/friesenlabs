# Copy to a real *.tfvars (gitignored) before apply. No secrets here.
aws_region = "us-east-1"
project    = "uplift"
vpc_cidr   = "10.0.0.0/16"
azs        = ["a", "b"]
ecr_repos  = ["api", "cube", "worker"]
