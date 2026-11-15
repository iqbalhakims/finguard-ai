variable "region" {
  description = "DigitalOcean region (sgp1 = Singapore)."
  type        = string
  default     = "sgp1"
}

variable "cluster_name" {
  description = "DOKS cluster name. The kubeconfig context becomes do-<region>-<cluster_name>."
  type        = string
  default     = "finguard"
}

variable "kubernetes_version_prefix" {
  description = "Kubernetes minor version, e.g. \"1.35.\" (the latest DOKS patch for it is used)."
  type        = string
  default     = "1.35."
}

variable "node_size" {
  description = "Droplet size for the worker nodes."
  type        = string
  default     = "s-2vcpu-4gb"
}

variable "node_count" {
  description = "Number of worker nodes."
  type        = number
  default     = 2
}

variable "prometheus_volume_size_gb" {
  description = "Size of the Prometheus block storage volume (GiB)."
  type        = number
  default     = 5
}
