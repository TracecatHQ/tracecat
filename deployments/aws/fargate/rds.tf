resource "aws_db_instance" "core_database" {
  identifier           = "core-database"
  engine               = "postgres"
  engine_version       = "16.3"
  instance_class       = "${var.db_instance_class}.${var.db_instance_size}"
  allocated_storage    = 20
  storage_type         = "gp2"
  username             = "postgres"
  multi_az              = true
  password = local.secrets.TRACECAT__DB_PASSWORD
  db_subnet_group_name = aws_db_subnet_group.tracecat_db_subnet.name
  vpc_security_group_ids = [aws_security_group.core_db.id]
  skip_final_snapshot  = true
}

resource "aws_db_instance" "temporal_database" {
  identifier           = "temporal-database"
  engine               = "postgres"
  engine_version       = "13.11"
  instance_class       = "${var.db_instance_class}.${var.db_instance_size}"
  allocated_storage    = 20
  storage_type         = "gp2"
  username             = "postgres"
  password = local.secrets.TEMPORAL__DB_PASSWORD
  multi_az              = true
  db_subnet_group_name = aws_db_subnet_group.tracecat_db_subnet.name
  vpc_security_group_ids = [aws_security_group.core_db]
  skip_final_snapshot  = true
}

resource "aws_db_subnet_group" "tracecat_db_subnet" {
  name       = "tracecat-db-subnet"
  subnet_ids = aws_subnet.private[*].id
}

locals {
  core_db_hostname = sensitive(split(":", aws_db_instance.core_database.endpoint)[0])
  temp_db_hostname = sensitive(split(":", aws_db_instance.temporal_database.endpoint)[0])
}
