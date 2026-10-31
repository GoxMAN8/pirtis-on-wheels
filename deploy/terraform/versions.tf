# Terraform or OpenTofu: nothing here is specific to either. The subscription comes from
# ARM_SUBSCRIPTION_ID (the deploy Makefile sets it from `az account show`), never the repo.
terraform {
  required_version = ">= 1.5"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.20, < 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  features {}
}
