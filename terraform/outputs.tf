output "cluster_id" {
  description = "DOKS cluster ID."
  value       = digitalocean_kubernetes_cluster.this.id
}

output "cluster_name" {
  value = digitalocean_kubernetes_cluster.this.name
}

output "kubernetes_version" {
  value = digitalocean_kubernetes_cluster.this.version
}

output "endpoint" {
  description = "Kubernetes API endpoint."
  value       = digitalocean_kubernetes_cluster.this.endpoint
}

output "vpc_id" {
  value = digitalocean_vpc.this.id
}

output "registry_endpoint" {
  description = "Image prefix the cluster can pull from (e.g. <endpoint>/finguard-api:sha-xxxxxxx)."
  value       = data.digitalocean_container_registry.this.endpoint
}

output "registry_storage_used_mb" {
  description = "DOCR storage in use; keep an eye on it against your subscription tier limit."
  value       = floor(data.digitalocean_container_registry.this.storage_usage_bytes / 1048576)
}

output "kube_context" {
  description = "Context name created by the kubeconfig command below."
  value       = "do-${var.region}-${var.cluster_name}"
}

output "kubeconfig_command" {
  description = "Fetch credentials locally (adds a context; does not switch to it with --set-current-context=false)."
  value       = "doctl kubernetes cluster kubeconfig save ${digitalocean_kubernetes_cluster.this.id} --set-current-context=false"
}
