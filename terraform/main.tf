# Single DOKS cluster for FinGuard AI: dedicated VPC, autoscaling node pool,
# DOCR integration (pulls registry.digitalocean.com/finai/* without secrets),
# automatic patch upgrades in a fixed maintenance window.

locals {
  tags = distinct(concat(["finguard", "terraform"], var.extra_tags))
}

# Latest DOKS patch release for the chosen minor version. Using the data source
# (instead of a hard-coded slug) avoids a diff every time auto-upgrade patches the cluster.
data "digitalocean_kubernetes_versions" "this" {
  version_prefix = var.kubernetes_version_prefix
}

# Fails the plan early if the registry does not exist; registry_integration needs it.
data "digitalocean_container_registry" "this" {
  name = var.registry_name
}

resource "digitalocean_vpc" "this" {
  name        = "${var.cluster_name}-${var.region}"
  region      = var.region
  ip_range    = var.vpc_ip_range
  description = "FinGuard AI DOKS network (managed by Terraform)"
}

resource "digitalocean_kubernetes_cluster" "this" {
  name     = var.cluster_name
  region   = var.region
  version  = data.digitalocean_kubernetes_versions.this.latest_version
  vpc_uuid = digitalocean_vpc.this.id

  ha                   = var.ha
  auto_upgrade         = true
  surge_upgrade        = true
  registry_integration = true
  tags                 = local.tags

  maintenance_policy {
    day        = var.maintenance_day
    start_time = var.maintenance_start_time
  }

  dynamic "control_plane_firewall" {
    for_each = length(var.control_plane_allowed_cidrs) > 0 ? [1] : []
    content {
      enabled           = true
      allowed_addresses = var.control_plane_allowed_cidrs
    }
  }

  node_pool {
    name       = "${var.cluster_name}-default"
    size       = var.node_size
    auto_scale = true
    min_nodes  = var.min_nodes
    max_nodes  = var.max_nodes
    tags       = local.tags
    labels = {
      "finguard.ai/pool" = "default"
    }
  }

  # The registry must exist before integration is enabled.
  depends_on = [data.digitalocean_container_registry.this]
}

resource "digitalocean_project" "this" {
  name        = var.project_name
  description = "FinGuard AI — banking fraud detection platform"
  purpose     = "Machine learning / AI / Data processing"
  environment = "Development"
  resources   = [digitalocean_kubernetes_cluster.this.urn]
}
