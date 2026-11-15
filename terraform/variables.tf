variable "region" {
  description = "DigitalOcean region for the VPC and cluster."
  type        = string
  default     = "sgp1"
}

variable "cluster_name" {
  description = "DOKS cluster name. The kubeconfig context becomes do-<region>-<cluster_name>."
  type        = string
  default     = "finguard"
}

variable "kubernetes_version_prefix" {
  description = "Kubernetes minor version to track, e.g. \"1.35.\". The latest DOKS patch for it is used, and auto-upgrade keeps it patched."
  type        = string
  default     = "1.35."

  validation {
    condition     = can(regex("^1\\.[0-9]+\\.$", var.kubernetes_version_prefix))
    error_message = "Use the form \"1.<minor>.\" (with the trailing dot)."
  }
}

variable "ha" {
  description = "High-availability control plane. Extra cost, and cannot be disabled once enabled. DOKS defaults this to true from 1.36, so it is set explicitly."
  type        = bool
  default     = false
}

variable "node_size" {
  description = "Droplet size for the default node pool. s-2vcpu-4gb fits the FinGuard stack (2 API replicas + Prometheus + Grafana) on two nodes."
  type        = string
  default     = "s-2vcpu-4gb"
}

variable "min_nodes" {
  description = "Minimum nodes in the autoscaling default pool (DOKS requires >= 1)."
  type        = number
  default     = 2

  validation {
    condition     = var.min_nodes >= 1
    error_message = "DOKS node pools need at least one node."
  }
}

variable "max_nodes" {
  description = "Maximum nodes in the autoscaling default pool."
  type        = number
  default     = 3

  validation {
    condition     = var.max_nodes >= var.min_nodes
    error_message = "max_nodes must be >= min_nodes."
  }
}

variable "vpc_ip_range" {
  description = "Optional CIDR for the dedicated VPC. Leave null to let DigitalOcean pick a non-overlapping range."
  type        = string
  default     = null
}

variable "control_plane_allowed_cidrs" {
  description = "If non-empty, enables the control plane firewall and only these CIDRs can reach the Kubernetes API (e.g. your office/home IP as x.x.x.x/32). HCP Terraform only talks to the DigitalOcean API, so it is unaffected."
  type        = list(string)
  default     = []
}

variable "registry_name" {
  description = "Existing DigitalOcean Container Registry the cluster pulls images from (CI pushes finguard-api there)."
  type        = string
  default     = "finai"
}

variable "maintenance_day" {
  description = "Day for automatic patch upgrades (monday..sunday or any)."
  type        = string
  default     = "sunday"
}

variable "maintenance_start_time" {
  description = "Maintenance window start, UTC HH:MM. 18:00 UTC = 02:00 in Singapore."
  type        = string
  default     = "18:00"
}

variable "project_name" {
  description = "DigitalOcean project that groups the cluster in the control panel."
  type        = string
  default     = "finguard-ai"
}

variable "extra_tags" {
  description = "Additional tags applied to the cluster and its nodes."
  type        = list(string)
  default     = []
}
