# Basic single DOKS cluster in Singapore for FinGuard AI.

data "digitalocean_kubernetes_versions" "this" {
  version_prefix = var.kubernetes_version_prefix
}

# The cluster is switched on/off with var.cluster_enabled (few hours a day); the
# Prometheus volume below is not, so metrics history survives every rebuild.
resource "digitalocean_kubernetes_cluster" "this" {
  count = var.cluster_enabled ? 1 : 0

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

  # Destroy order: remove the cluster (which detaches the volumes) before the volumes.
  depends_on = [digitalocean_volume.prometheus, digitalocean_volume.loki]
}

# The cluster resource gained `count`; keep any existing state entry instead of
# planning a replacement.
moved {
  from = digitalocean_kubernetes_cluster.this
  to   = digitalocean_kubernetes_cluster.this[0]
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

  lifecycle {
    # The DigitalOcean CSI driver tags the volume "k8s:<cluster-id>" when a cluster
    # attaches it; every rebuild adds a new one. Don't fight it on each apply.
    ignore_changes = [tags]
  }
}

# Persistent disk for Loki (logs), mounted the same way through a static
# PersistentVolume in k8s/values/loki.yaml (volumeHandle = loki_volume_id output,
# filled in by `make doks-volume`). Separate from Prometheus so a burst of logs can
# never fill the metrics disk.
resource "digitalocean_volume" "loki" {
  name                    = "${var.cluster_name}-loki"
  region                  = var.region
  size                    = var.loki_volume_size_gb
  initial_filesystem_type = "ext4"
  description             = "FinGuard AI Loki logs (7 days)"

  lifecycle {
    ignore_changes = [tags] # CSI "k8s:<cluster-id>" tags, as for the Prometheus volume
  }
}
