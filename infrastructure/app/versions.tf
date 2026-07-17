terraform {
  required_version = "~> 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # Terraform backends control where and how your state file is stored. Local backends are fine 
  # for development, but any team or CI/CD setup needs a remote backend like S3, Azure Blob 
  # Storage, or GCS for state locking, durability, and shared access.
  backend "s3" {
    bucket         = "a-game-tfstate"
    key            = "app/dev/terraform.tfstate"
    region         = "us-east-1"
    use_lockfile   = true

    access_key = "test"
    secret_key = "test"

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