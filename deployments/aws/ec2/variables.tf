variable "aws_region" {
  description = "The AWS region to deploy resources in"
  type        = string
  default     = "us-west-2"
}

variable "aws_availability_zone" {
  description = "The AWS availability zone to deploy resources in"
  type        = string
  default     = "us-west-2a"
}

variable "project_name" {
  description = "The name of the project"
  type        = string
  default     = "tracecat"
}

variable "environment" {
  description = "The environment (e.g., dev, staging, prod). Used for tagging."
  type        = string
  default     = "prod"
}

variable "vpc_cidr" {
  description = "The CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "private_subnet_cidr" {
  description = "The CIDR block for the private subnet"
  type        = string
  default     = "10.0.1.0/24"
}

variable "public_subnet_cidr" {
  description = "The CIDR block for the public subnet"
  type        = string
  default     = "10.0.2.0/24"
}

variable "instance_type" {
  description = "The type of EC2 instance to launch"
  type        = string
  default     = "t3.large"
}

variable "TFC_CONFIGURATION_VERSION_GIT_COMMIT_SHA" {
  description = "Terraform Cloud only: the git commit SHA of that triggered the run"
  type        = string
  default     = null
}

variable "tracecat_image_tag" {
  description = "The version of Tracecat to use"
  type        = string
  default     = "0.9.0"
}
