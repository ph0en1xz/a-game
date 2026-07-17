provider "aws" {
  region     = "us-east-1"
  access_key = "test"
  secret_key = "test"

  # Point every AWS call at LocalStack instead of real AWS.
  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true

  endpoints {
    s3  = "http://localhost:4566"
    sts = "http://localhost:4566"
    iam = "http://localhost:4566"
    ec2 = "http://localhost:4566" # VPC / subnets / IGW / NAT all use the EC2 API
  }
}
