output "cluster_id" {
  value = digitalocean_kubernetes_cluster.this.id
}

output "endpoint" {
  value = digitalocean_kubernetes_cluster.this.endpoint
}

output "kube_context" {
  value = "do-${var.region}-${var.cluster_name}"
}

output "kubeconfig_command" {
  description = "Adds the cluster to your kubeconfig without switching your current context."
  value       = "doctl kubernetes cluster kubeconfig save ${digitalocean_kubernetes_cluster.this.id} --set-current-context=false"
}
