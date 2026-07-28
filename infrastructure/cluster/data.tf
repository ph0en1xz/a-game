data "terraform_remote_state" "network" {
  backend = "s3"
  config = {
    bucket = "a-game-tfstate"
    key    = "network/dev/terraform.tfstate"
    region = "eu-west-1"

    access_key                  = "test"
    secret_key                  = "test"
    use_path_style              = true
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true

    endpoints = {
      s3 = "http://localhost:4566"
    }
  }
}