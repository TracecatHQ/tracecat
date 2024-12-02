## AWS provider variables

variable "aws_region" {
  type        = string
  description = "AWS region (secrets and hosted zone must be in the same region)"
}

variable "az_count" {
  type        = number
  description = "Number of AZs to cover in a given region"
  default     = 2
}

## DNS

variable "domain_name" {
  type        = string
  description = "The domain name to use for the application"
}

variable "hosted_zone_id" {
  type        = string
  description = "The hosted zone ID to use for the domain name"
}
