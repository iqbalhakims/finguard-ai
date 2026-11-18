output "cluster_id" {
  description = "Null while cluster_enabled = false."
  value       = one(digitalocean_kubernetes_cluster.this[*].id)
}

output "endpoint" {
  value = one(digitalocean_kubernetes_cluster.this[*].endpoint)
}

output "kube_context" {
  value = "do-${var.region}-${var.cluster_name}"
}

output "kubeconfig_command" {
  description = "Adds the cluster to your kubeconfig without switching your current context (make doks-up runs it)."
  value       = "doctl kubernetes cluster kubeconfig save ${var.cluster_name} --set-current-context=false"
}

output "prometheus_volume_id" {
  description = "PersistentVolume volumeHandle in k8s/values/kube-prometheus-stack.yaml (set by make doks-volume)."
  value       = digitalocean_volume.prometheus.id
}
