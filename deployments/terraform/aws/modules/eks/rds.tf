# RDS Security Group
resource "aws_security_group" "rds" {
  name        = "${var.cluster_name}-rds-sg"
  description = "Security group for RDS PostgreSQL"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.tracecat_postgres_client.id]
    description     = "PostgreSQL from Tracecat pods with SecurityGroupPolicy"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-rds-sg"
  })
}

# Pod security group for PostgreSQL access (used by SecurityGroupPolicy)
resource "aws_security_group" "tracecat_postgres_client" {
  name        = "${var.cluster_name}-postgres-client-sg"
  description = "Pod security group for Tracecat PostgreSQL access"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [data.aws_vpc.selected.cidr_block]
    description = "Allow intra-VPC traffic to Tracecat pods"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-postgres-client-sg"
  })
}

# RDS Subnet Group
resource "aws_db_subnet_group" "tracecat" {
  name       = "${var.cluster_name}-db-subnet-group"
  subnet_ids = var.private_subnet_ids

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-db-subnet-group"
  })
}

# RDS PostgreSQL Instance
resource "aws_db_instance" "tracecat" {
  identifier = "${var.cluster_name}-postgres-${local.rds_suffix}"

  engine                = "postgres"
  engine_version        = "16.6"
  instance_class        = var.rds_instance_class
  allocated_storage     = var.rds_allocated_storage
  max_allocated_storage = var.rds_allocated_storage * 5
  storage_type          = var.rds_storage_type

  snapshot_identifier = var.rds_snapshot_identifier != "" ? var.rds_snapshot_identifier : null

  db_name                     = var.rds_snapshot_identifier == "" ? "tracecat" : null
  username                    = var.rds_snapshot_identifier == "" ? var.rds_master_username : null
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.tracecat.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  publicly_accessible = false
  multi_az            = true
  storage_encrypted   = true

  database_insights_mode = var.rds_database_insights_mode

  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "Mon:04:00-Mon:05:00"

  skip_final_snapshot       = var.rds_skip_final_snapshot
  final_snapshot_identifier = var.rds_skip_final_snapshot ? null : "${var.cluster_name}-postgres-${local.rds_suffix}-final"
  deletion_protection       = var.rds_deletion_protection

  performance_insights_enabled          = true
  performance_insights_retention_period = var.rds_database_insights_mode == "advanced" ? 465 : 7

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-postgres-${local.rds_suffix}"
  })
}

resource "aws_secretsmanager_secret_rotation" "rds_master_password" {
  secret_id = aws_db_instance.tracecat.master_user_secret[0].secret_arn

  rotation_rules {
    schedule_expression = var.rds_password_rotation_schedule
  }
}

# Create additional databases for Temporal using a Kubernetes job
resource "kubernetes_job_v1" "create_temporal_databases" {
  count = var.temporal_mode == "self-hosted" ? 1 : 0

  metadata {
    name      = "temporal-db-setup"
    namespace = kubernetes_namespace.tracecat.metadata[0].name
    labels = {
      "tracecat.com/access-postgres" = "true"
    }
  }

  spec {
    ttl_seconds_after_finished = 300

    template {
      metadata {
        labels = {
          "tracecat.com/access-postgres" = "true"
        }
      }

      spec {
        restart_policy = "Never"

        container {
          name  = "db-setup"
          image = "postgres:16"

          env {
            name  = "PGHOST"
            value = aws_db_instance.tracecat.address
          }

          env {
            name  = "PGUSER"
            value = var.rds_master_username
          }

          env {
            name  = "PGDATABASE"
            value = "tracecat"
          }

          env {
            name = "PGPASSWORD"
            value_from {
              secret_key_ref {
                name = "tracecat-postgres-credentials"
                key  = "password"
              }
            }
          }

          command = [
            "/bin/sh",
            "-c",
            <<-EOT
            psql -c "SELECT 1 FROM pg_database WHERE datname = 'temporal'" | grep -q 1 || psql -c "CREATE DATABASE temporal"
            psql -c "SELECT 1 FROM pg_database WHERE datname = 'temporal_visibility'" | grep -q 1 || psql -c "CREATE DATABASE temporal_visibility"
            echo "Temporal databases created successfully"
            EOT
          ]
        }
      }
    }
  }

  wait_for_completion = true

  timeouts {
    create = "10m"
  }

  depends_on = [
    aws_db_instance.tracecat,
    kubernetes_manifest.tracecat_postgres_sg_policy,
    kubernetes_manifest.postgres_credentials_external_secret,
    kubernetes_namespace.tracecat
  ]
}
