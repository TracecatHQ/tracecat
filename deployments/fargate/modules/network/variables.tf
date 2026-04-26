## AWS provider variables

variable "aws_region" {
  type        = string
  description = "AWS region (secrets and hosted zone must be in the same region)"
}

variable "name_prefix" {
  type        = string
  description = "Prefix for network resource names"
  default     = "tracecat"
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR block for the Tracecat VPC"
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  type        = list(string)
  description = "CIDR blocks for public subnets"
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  type        = list(string)
  description = "CIDR blocks for private subnets"
  default     = ["10.0.10.0/24", "10.0.11.0/24"]
}

## DNS

variable "domain_name" {
  type        = string
  description = "The domain name to use for Tracecat"
}

variable "hosted_zone_id" {
  type        = string
  description = "The hosted zone ID associated with the Tracecat domain"
}
