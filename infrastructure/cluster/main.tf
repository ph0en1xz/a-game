####################################################################################
###  EKS Cluster Configuration
##################################################################

resource "aws_eks_cluster" "cluster" {
  name = var.eks_cluster_name
  depends_on = [
    aws_iam_role_policy_attachment.cluster_AmazonEKSClusterPolicy
  ]
  role_arn = aws_iam_role.cluster.arn
  vpc_config {
    subnet_ids = [

    ]
  }
}

resource "aws_iam_role" "cluster" {
  name = "eks-cluster-a-game"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = [
        "sts:AssumeRole",
        "sts:TagSession"
      ]
      Effect = "Allow"
      Principal = {
        Service = "eks.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cluster_AmazonEKSClusterPolicy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_eks_cluster.cluster
}