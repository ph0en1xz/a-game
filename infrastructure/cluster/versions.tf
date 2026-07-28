terraform {
  required_version = "~> 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # This layer keeps its OWN state file (its own blast radius).
  # cluster/ and app/ each get their own key too.
  backend "s3" {
    bucket = "a-game-tfstate"
    key    = "cluster/dev/terraform.tfstate"
    region = "eu-west-1"

    use_lockfile = true # S3-native locking (no DynamoDB)

    # LocalStack dummy creds + endpoint redirects (local-only)
    access_key                  = "test"
    secret_key                  = "test"
    use_path_style              = true
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true

    endpoints = {
      s3  = "http://localhost:4566"
      iam = "http://localhost:4566"
      sts = "http://localhost:4566"
    }
  }
}
