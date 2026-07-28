# The cluster layer's public API — what the app layer reads via terraform_remote_state.
# Together these three are exactly what a kubernetes/helm provider needs to authenticate.

# Which cluster to talk to.
output "cluster_name" {
  description = "EKS cluster name"
  value       = aws_eks_cluster.cluster.name
}

# The Kubernetes API server URL — the address kubectl and Helm actually send requests to.
output "cluster_endpoint" {
  description = "EKS API server endpoint"
  value       = aws_eks_cluster.cluster.endpoint
}

# The cluster's CA certificate (base64). The client uses it to verify it is talking to the
# real API server and not something impersonating it — the TLS trust half of the connection.
output "cluster_ca_data" {
  description = "Base64-encoded CA certificate for the EKS API server"
  value       = aws_eks_cluster.cluster.certificate_authority[0].data
}
