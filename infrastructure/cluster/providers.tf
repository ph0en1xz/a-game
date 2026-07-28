provider "aws" {
  region     = var.region
  access_key = "test"
  secret_key = "test"

  # Point every AWS call at LocalStack instead of real AWS.
  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    s3  = "http://localhost:4566"
    sts = "http://localhost:4566"
    iam = "http://localhost:4566"
    ec2 = "http://localhost:4566"
    eks = "http://localhost:4566"
  }

  # default_tags = every resource gets tagged automatically, so you never forget one.
  default_tags {
    tags = {
      project     = var.project
      environment = var.environment
      managed-by  = "terraform"
    }
  }
}
