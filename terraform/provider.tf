# Credentials come from the DIGITALOCEAN_TOKEN environment variable, set as a
# sensitive env var on the HCP Terraform workspace. Never hard-code tokens here.
provider "digitalocean" {}
