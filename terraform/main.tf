# Basic single DOKS cluster in Singapore for FinGuard AI.

data "digitalocean_kubernetes_versions" "this" {
  version_prefix = var.kubernetes_version_prefix
}

resource "digitalocean_kubernetes_cluster" "this" {
  name    = var.cluster_name
  region  = var.region
  version = data.digitalocean_kubernetes_versions.this.latest_version

  # Keep the standard (non-HA) control plane: DOKS enables HA by default from 1.36.
  ha = false

  # Lets pods pull registry.digitalocean.com/finai/* without image pull secrets.
  registry_integration = true

  node_pool {
    name       = "${var.cluster_name}-pool"
    size       = var.node_size
    node_count = var.node_count
  }

  # Destroy order: remove the cluster (which detaches the volume) before the volume.
  depends_on = [digitalocean_volume.prometheus]
}

# Persistent disk for Prometheus. Kubernetes mounts it through a static
# PersistentVolume in k8s/values/kube-prometheus-stack.yaml (volumeHandle =
# prometheus_volume_id output, filled in by `make doks-volume`),
# so the disk's lifecycle stays in Terraform, not in the cluster.
resource "digitalocean_volume" "prometheus" {
  name                    = "${var.cluster_name}-prometheus"
  region                  = var.region
  size                    = var.prometheus_volume_size_gb
  initial_filesystem_type = "ext4"
  description             = "FinGuard AI Prometheus metrics (TSDB)"
}
