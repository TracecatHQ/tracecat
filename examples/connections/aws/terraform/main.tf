terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.73.0"
    }
  }
}

variable "region" { type = string }
variable "tracecat_principal_arn" { type = string }

variable "external_id" {
  type      = string
  sensitive = true
}

variable "enable_guardduty_triage" {
  type    = bool
  default = false
}

variable "entry_stack_name" {
  type    = string
  default = "tracecat-secops-entry"
}

variable "entry_role_name" {
  type    = string
  default = "TracecatSecOpsAccessRole"
}

variable "organization" {
  type = object({
    target_ou_id            = string
    member_account_ids      = set(string)
    management_account_id   = optional(string)
    previous_entry_role_arn = optional(string)
  })
  default = null

  validation {
    condition = var.organization == null ? true : (
      trimspace(var.organization.target_ou_id) != "" && length(var.organization.member_account_ids) > 0
    )
    error_message = "Organization mode requires a root or OU ID and at least one member account."
  }
}

provider "aws" {
  alias   = "entry"
  profile = "aws-security"
  region  = var.region
}

provider "aws" {
  alias   = "stacksets"
  profile = var.organization == null ? "aws-security" : "aws-stacksets"
  region  = var.region
}

provider "aws" {
  alias               = "management"
  allowed_account_ids = local.management_account_id == null ? null : [local.management_account_id]
  profile             = local.management_account_id == null ? "aws-security" : "aws-management"
  region              = var.region
}

data "aws_partition" "current" {
  provider = aws.entry
}

locals {
  audit_template        = var.organization == null ? null : file("${path.module}/../cloudformation/tracecat-secops-audit-role.yaml")
  member_account_ids    = var.organization == null ? [] : sort(tolist(var.organization.member_account_ids))
  management_account_id = try(var.organization.management_account_id, null)
  target_account_ids = concat(
    local.member_account_ids,
    local.management_account_id == null ? [] : [local.management_account_id],
  )
  target_role_arns = [
    for account_id in local.target_account_ids :
    "arn:${data.aws_partition.current.partition}:iam::${account_id}:role/TracecatSecOpsAuditRole"
  ]
  entry_role_arns = join(",", compact([
    aws_cloudformation_stack.entry.outputs["RoleArn"],
    try(var.organization.previous_entry_role_arn, null),
  ]))
}

resource "aws_cloudformation_stack" "entry" {
  provider      = aws.entry
  name          = var.entry_stack_name
  template_body = file("${path.module}/../cloudformation/tracecat-secops-access-role.yaml")
  capabilities  = ["CAPABILITY_NAMED_IAM"]
  parameters = {
    EntryRoleName         = var.entry_role_name
    TracecatPrincipalArn  = var.tracecat_principal_arn
    ExternalId            = var.external_id
    TargetRoleArns        = join(",", local.target_role_arns)
    EnableGuardDutyTriage = tostring(var.enable_guardduty_triage)
  }
}

resource "aws_cloudformation_stack_set" "audit" {
  count            = var.organization == null ? 0 : 1
  provider         = aws.stacksets
  name             = "tracecat-secops-audit"
  permission_model = "SERVICE_MANAGED"
  call_as          = "DELEGATED_ADMIN"
  capabilities     = ["CAPABILITY_NAMED_IAM"]
  template_body    = local.audit_template
  parameters       = { EntryRoleArns = local.entry_role_arns }

  auto_deployment {
    enabled = false
  }
}

resource "aws_cloudformation_stack_instances" "members" {
  count          = var.organization == null ? 0 : 1
  provider       = aws.stacksets
  stack_set_name = aws_cloudformation_stack_set.audit[0].name
  call_as        = "DELEGATED_ADMIN"
  regions        = [var.region]

  deployment_targets {
    organizational_unit_ids = [var.organization.target_ou_id]
    account_filter_type     = "INTERSECTION"
    accounts                = local.member_account_ids
  }
}

resource "aws_cloudformation_stack" "management" {
  count         = local.management_account_id == null ? 0 : 1
  provider      = aws.management
  name          = "tracecat-secops-audit"
  template_body = local.audit_template
  capabilities  = ["CAPABILITY_NAMED_IAM"]
  parameters    = { EntryRoleArns = local.entry_role_arns }
}

output "role_arn" {
  value = aws_cloudformation_stack.entry.outputs["RoleArn"]
}
