####################################################################################
###  EKS Cluster Configuration
##################################################################

resource "aws_eks_cluster" "cluster" {
  name     = var.eks_cluster_name
  role_arn = aws_iam_role.cluster.arn
  depends_on = [
    aws_iam_role_policy_attachment.cluster_AmazonEKSClusterPolicy
  ]
  vpc_config {
    subnet_ids = data.terraform_remote_state.network.outputs.private_subnet_ids
  }
  version = "1.32"
}

resource "aws_eks_node_group" "node_group_config" {
  node_group_name = "${var.project}-${var.environment}-nodes"
  subnet_ids      = data.terraform_remote_state.network.outputs.private_subnet_ids
  cluster_name    = aws_eks_cluster.cluster.name
  node_role_arn   = aws_iam_role.node.arn
  scaling_config {
    desired_size = 1
    max_size     = 2
    min_size     = 1
  }
  depends_on = [
    aws_iam_role_policy_attachment.worker_node_policy,
    aws_iam_role_policy_attachment.ecr_readonly,
    aws_iam_role_policy_attachment.cni_policy
  ]
  instance_types = ["t3.small"]
}

resource "aws_iam_role" "node" {
  name = "eks-node-a-game"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })
}

# Attach Worker Node Policy
resource "aws_iam_role_policy_attachment" "worker_node_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
  role       = aws_iam_role.node.name
}

# Attach ECR Read-Only Policy
resource "aws_iam_role_policy_attachment" "ecr_readonly" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.node.name
}

# gives the Amazon VPC CNI plugin (the networking engine inside your cluster) permission 
# to manage real AWS network resources
resource "aws_iam_role_policy_attachment" "cni_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
  role       = aws_iam_role.node.name
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
  role       = aws_iam_role.cluster.name
}