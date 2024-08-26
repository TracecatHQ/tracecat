data "aws_availability_zones" "this" {
  state = "available"
}

resource "aws_vpc" "tracecat" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "tracecat-vpc"
  }
}

# CloudMap Namespace for Service Connect
resource "aws_service_discovery_private_dns_namespace" "namespace" {
  name        = "tracecat.local"
  description = "Private DNS namespace for ECS services"
  vpc         = aws_vpc.tracecat.id
}

resource "aws_subnet" "public" {
  count             = var.az_count 
  vpc_id            = aws_vpc.tracecat.id
  cidr_block        = "10.0.${count.index + 1}.0/24"
  availability_zone = data.aws_availability_zones.this.names[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "tracecat-public-subnet-${count.index + 1}"
  }
}

resource "aws_internet_gateway" "gw" {
    vpc_id = aws_vpc.tracecat.id
}

resource "aws_route" "internet_access" {
    route_table_id         = aws_vpc.tracecat.main_route_table_id
    destination_cidr_block = "0.0.0.0/0"
    gateway_id             = aws_internet_gateway.gw.id
}

resource "aws_eip" "gw" {
    count      = var.az_count
    domain = "vpc"
    depends_on = [aws_internet_gateway.gw]
}

resource "aws_nat_gateway" "gw" {
    count         = var.az_count
    subnet_id     = element(aws_subnet.public.*.id, count.index)
    allocation_id = element(aws_eip.gw.*.id, count.index)
}

resource "aws_route_table" "private" {
    count  = var.az_count
    vpc_id = aws_vpc.tracecat.id

    route {
        cidr_block     = "0.0.0.0/0"
        nat_gateway_id = element(aws_nat_gateway.gw.*.id, count.index)
    }
}

resource "aws_route_table_association" "private" {
    count          = var.az_count
    subnet_id      = element(aws_subnet.private.*.id, count.index)
    route_table_id = element(aws_route_table.private.*.id, count.index)
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.tracecat.id
  cidr_block        = "10.0.${count.index + 10}.0/24"
  availability_zone = data.aws_availability_zones.this.names[count.index]

  tags = {
    Name = "tracecat-private-subnet-${count.index + 1}"
  }
}

resource "aws_route_table" "public_rt" {
  vpc_id = aws_vpc.tracecat.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }

  tags = {
    Name = "tracecat-public-rt"
  }
}

resource "aws_route_table_association" "public_subnet_routes" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public_rt.id
}

# 
