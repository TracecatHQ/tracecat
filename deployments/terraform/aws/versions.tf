terraform {
  # NOTE: Terraform 1.14 currently breaks planning for this stack in TFC because
  # the kubernetes/helm providers attempt to initialize before EKS connection
  # details exist, falling back to localhost and failing with cluster-unreachable.
  required_version = ">= 1.11.0, < 1.14.0"

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
    http = {
      source  = "hashicorp/http"
      version = "3.4.0"
    }
  }
}

locals {
  aws_role_arn = var.aws_role_name != null && var.aws_account_id != null ? "arn:aws:iam::${var.aws_account_id}:role/${var.aws_role_name}" : null
  eks_get_token_args = concat(
    ["eks", "get-token", "--cluster-name", module.eks.cluster_name, "--region", var.aws_region],
    local.aws_role_arn != null ? ["--role-arn", local.aws_role_arn] : []
  )
}

check "aws_role_requires_account_id" {
  assert {
    condition     = var.aws_role_name == null || var.aws_account_id != null
    error_message = "aws_account_id must be set when aws_role_name is provided."
  }
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

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = local.eks_get_token_args
  }
}

provider "helm" {
  kubernetes {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)

    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = local.eks_get_token_args
    }
  }
}
