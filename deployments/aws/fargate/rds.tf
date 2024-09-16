resource "aws_db_instance" "core_database" {
  identifier                   = "core-database"
  engine                       = "postgres"
  engine_version               = "16.3"
  instance_class               = "${var.db_instance_class}.${var.db_instance_size}"
  allocated_storage            = 5
  storage_type                 = "gp2"
  username                     = "postgres"
  multi_az                     = var.rds_multi_az
  manage_master_user_password  = true
  db_subnet_group_name         = aws_db_subnet_group.tracecat_db_subnet.name
  vpc_security_group_ids       = [aws_security_group.core_db.id]
  skip_final_snapshot          = var.rds_skip_final_snapshot
  deletion_protection          = var.rds_deletion_protection
  apply_immediately            = var.rds_apply_immediately
  backup_retention_period      = var.rds_backup_retention_period
  performance_insights_enabled = var.rds_performance_insights_enabled
  auto_minor_version_upgrade   = var.rds_auto_minor_version_upgrade
}

resource "aws_db_instance" "temporal_database" {
  identifier                   = "temporal-database"
  engine                       = "postgres"
  engine_version               = "13.15"
  instance_class               = "${var.db_instance_class}.${var.db_instance_size}"
  allocated_storage            = 5
  storage_type                 = "gp2"
  username                     = "postgres"
  manage_master_user_password  = true
  multi_az                     = var.rds_multi_az
  db_subnet_group_name         = aws_db_subnet_group.tracecat_db_subnet.name
  vpc_security_group_ids       = [aws_security_group.temporal_db.id]
  skip_final_snapshot          = var.rds_skip_final_snapshot
  deletion_protection          = var.rds_deletion_protection
  apply_immediately            = var.rds_apply_immediately
  backup_retention_period      = var.rds_backup_retention_period
  performance_insights_enabled = var.rds_performance_insights_enabled
  auto_minor_version_upgrade   = var.rds_auto_minor_version_upgrade
}

resource "aws_db_subnet_group" "tracecat_db_subnet" {
  name       = "tracecat-db-subnet"
  subnet_ids = aws_subnet.private[*].id
}

# Local variables for database hostnames
locals {
  core_db_hostname = sensitive(split(":", aws_db_instance.core_database.endpoint)[0])
  temp_db_hostname = sensitive(split(":", aws_db_instance.temporal_database.endpoint)[0])
}
