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

# The VPC CNI ships with NetworkPolicy enforcement OFF. Without this addon block
# every policy in k8s/50-networkpolicies.yaml applies cleanly, shows up in
# `kubectl get netpol`, and blocks nothing - the API server stores the object,
# but enforcement is the CNI's job and it simply declines to do it. There is no
# warning and no error. The namespace's default-deny silently becomes allow-all.
#
# k3d does not have this problem because it runs kube-router, which enforces via
# iptables. That difference is invisible until you move to EKS.
#
# Requires VPC CNI >= 1.14 (addon_version left unset = the default for the
# cluster version, which on 1.32 is well past that) and the node role's
# AmazonEKS_CNI_Policy, already attached below.
#
# Verify after apply - do not infer it from the object existing:
#   kubectl run probe --image=busybox -n a-game --rm -it -- wget -qO- a-game-postgres:5432
# It must fail. If it connects, enforcement is off.
# Under test
resource "aws_eks_addon" "vpc_cni" {
  cluster_name = aws_eks_cluster.cluster.name
  addon_name   = "vpc-cni"

  # Strings, not booleans - the addon's config schema types these as strings and
  # a real bool is rejected.
  configuration_values = jsonencode({
    enableNetworkPolicy = "true"
  })

  # The addon is preinstalled by EKS, so the first apply is always a conflict
  # with a config Terraform did not write. OVERWRITE makes this layer the owner.
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  # Nodes first: the agent this enables is a DaemonSet and has nowhere to land
  # until at least one node has joined.
  depends_on = [aws_eks_node_group.node_group_config]
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