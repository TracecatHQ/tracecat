data "aws_availability_zones" "available" {
  state = "available"
}

variable "aws_region" {
  default = "us-east-2"
}
