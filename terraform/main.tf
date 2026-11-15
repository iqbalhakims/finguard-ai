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
}
