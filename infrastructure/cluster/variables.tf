variable "eks_cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "a-game-eks"
}

variable "project" {
  description = "Project name, used in tags and resource names"
  type        = string
  default     = "a-game"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "dev"
}

variable "region" {
  description = "Region where the app is hosted"
  type        = string
  default     = "eu-west-1"
}
