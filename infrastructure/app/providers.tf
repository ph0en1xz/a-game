provider "aws" {
  region     = "us-east-1"
  access_key = "test"
  secret_key = "test"

  # LocalStack-specific: skip the calls that only make sense against real AWS
  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
  
  # Point every AWS API call at LocalStack's single endpoint
  endpoints {
    s3  = "http://localhost:4566"
    sts = "http://localhost:4566"
    iam = "http://localhost:4566"
  }
}