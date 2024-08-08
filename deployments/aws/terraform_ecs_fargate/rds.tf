variable "instance_class" {
  type    = string
  default = "db.t3"
}

variable "instance_size" {
  type    = string
  default = "medium" 
}

resource "aws_db_instance" "core_database" {
  identifier           = "core-database"
  engine               = "postgres"
  engine_version       = "16.3"
  instance_class       = "${var.instance_class}.${var.instance_size}"
  allocated_storage    = 20
  storage_type         = "gp2"
  username             = "postgres"
  multi_az              = true
  password             = var.db_pass_value 
  db_subnet_group_name = aws_db_subnet_group.tracecat_db_subnet.name
  vpc_security_group_ids = [aws_security_group.core_db_security.id]
  skip_final_snapshot  = true
}

resource "aws_db_instance" "temporal_database" {
  identifier           = "temporal-database"
  engine               = "postgres"
  engine_version       = "13.11"
  instance_class       = "${var.instance_class}.${var.instance_size}"
  allocated_storage    = 20
  storage_type         = "gp2"
  username             = "postgres"
  password             = var.db_pass_value 
  multi_az              = true
  db_subnet_group_name = aws_db_subnet_group.tracecat_db_subnet.name
  vpc_security_group_ids = [aws_security_group.core_db_security.id]
  skip_final_snapshot  = true
}

resource "aws_db_subnet_group" "tracecat_db_subnet" {
  name       = "tracecat-db-subnet"
  subnet_ids = aws_subnet.private[*].id
}
