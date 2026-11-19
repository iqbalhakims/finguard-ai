variable "cluster_enabled" {
  description = "Run the cluster? Set false (HCP workspace variable) to tear it down for the day; volumes are kept. Run `make doks-down` first."
  type        = bool
  default     = true
}

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
  description = "Droplet size for the worker nodes. sgp1 is often out of the regular s-* sizes; the -amd variants are the same spec (doctl compute region list shows what is available)."
  type        = string
  default     = "g-2vcpu-8gb" # 8 GB for Argo CD + kube-prometheus-stack + OTel; sgp1 had no s-4vcpu-8gb-amd capacity (2026-11-19)
}

variable "node_count" {
  description = "Number of worker nodes."
  type        = number
  default     = 1
}

variable "prometheus_volume_size_gb" {
  description = "Size of the Prometheus block storage volume (GiB)."
  type        = number
  default     = 5
}
