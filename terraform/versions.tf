terraform {
  required_version = ">= 1.10"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.106"
    }
  }

  # HCP Terraform: state + remote runs. `terraform init` creates the workspace
  # if it does not exist yet. The workspace needs a sensitive environment
  # variable DIGITALOCEAN_TOKEN (see README > Infrastructure).
  cloud {
    organization = "iqbal-hakim"

    workspaces {
      name = "finguard-doks"
    }
  }
}
