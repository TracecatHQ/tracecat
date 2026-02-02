terraform {
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.27.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "2.35.1"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "2.17.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "4.0.4"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.7.2"
    }
    null = {
      source  = "hashicorp/null"
      version = "3.2.4"
    }
    http = {
      source  = "hashicorp/http"
      version = "3.4.0"
    }
  }
}

locals {
  aws_role_arn = var.aws_role_name != null && var.aws_account_id != null ? "arn:aws:iam::${var.aws_account_id}:role/${var.aws_role_name}" : null
}

provider "aws" {
  region = var.aws_region

  dynamic "assume_role" {
    for_each = local.aws_role_arn != null ? [1] : []
    content {
      role_arn = local.aws_role_arn
    }
  }

  default_tags {
    tags = var.tags
  }
}

provider "kubernetes" {
  host                   = module.eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
  token                  = module.eks.cluster_auth_token
}

provider "helm" {
  kubernetes {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
    token                  = module.eks.cluster_auth_token
  }
}
