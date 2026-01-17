variable "aws_region" {
  description = "AWS region for the infrastructure"
  type        = string
}

variable "domain_name" {
  description = "Domain name for Tracecat (e.g., tracecat.example.com)"
  type        = string
}

variable "hosted_zone_id" {
  description = "Route53 hosted zone ID for DNS validation"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones to use"
  type        = number
  default     = 2
}

variable "cluster_name" {
  description = "Name of the EKS cluster for subnet tagging"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
