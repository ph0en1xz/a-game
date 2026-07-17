resource "aws_s3_bucket" "artifacts" {
  bucket = "a-game-artifacts"

  tags = {
    managed-by = "terraform"
  }
}