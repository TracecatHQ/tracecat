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

  # Optional fallback for pods without SecurityGroupPolicy (off by default).
  dynamic "ingress" {
    for_each = var.rds_allow_vpc_cidr_fallback ? [1] : []
    content {
      from_port   = 5432
      to_port     = 5432
      protocol    = "tcp"
      cidr_blocks = [data.aws_vpc.selected.cidr_block]
      description = "PostgreSQL from VPC CIDR (fallback)"
    }
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

  snapshot_identifier = var.rds_snapshot_identifier != "" ? var.rds_snapshot_identifier : null

  db_name                     = var.rds_snapshot_identifier == "" ? "tracecat" : null
  username                    = var.rds_snapshot_identifier == "" ? var.rds_master_username : null
  manage_master_user_password = true

  master_user_secret_rotation {
    automatically_after = "365d"
  }

  db_subnet_group_name   = aws_db_subnet_group.tracecat.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  publicly_accessible = false
  multi_az            = false
  storage_encrypted   = true

  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "Mon:04:00-Mon:05:00"

  skip_final_snapshot       = var.rds_skip_final_snapshot
  final_snapshot_identifier = var.rds_skip_final_snapshot ? null : "${var.cluster_name}-postgres-${local.rds_suffix}-final"
  deletion_protection       = var.rds_deletion_protection

  performance_insights_enabled = true

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-postgres-${local.rds_suffix}"
  })
}

# Create additional databases for Temporal using a Kubernetes job
resource "null_resource" "create_temporal_databases" {
  count = var.temporal_mode == "self-hosted" ? 1 : 0

  triggers = {
    rds_address = aws_db_instance.tracecat.address
    namespace   = kubernetes_namespace.tracecat.metadata[0].name
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      # Wait for ExternalSecret to exist and sync the credentials before creating the job.
      for i in $(seq 1 12); do
        if kubectl get externalsecrets.external-secrets.io tracecat-postgres-secrets -n ${kubernetes_namespace.tracecat.metadata[0].name} >/dev/null 2>&1; then
          break
        fi
        echo "Waiting for tracecat-postgres-secrets ExternalSecret... ($i/12)"
        sleep 5
      done

      echo "Waiting for tracecat-postgres-secrets to be ready..."
      if ! kubectl wait --for=condition=Ready externalsecrets.external-secrets.io/tracecat-postgres-secrets -n ${kubernetes_namespace.tracecat.metadata[0].name} --timeout=120s; then
        echo "ExternalSecret tracecat-postgres-secrets did not become ready"
        kubectl describe externalsecrets.external-secrets.io tracecat-postgres-secrets -n ${kubernetes_namespace.tracecat.metadata[0].name} || true
        exit 1
      fi

      kubectl delete job temporal-db-setup -n ${kubernetes_namespace.tracecat.metadata[0].name} --ignore-not-found

      cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: temporal-db-setup
  namespace: ${kubernetes_namespace.tracecat.metadata[0].name}
  labels:
    tracecat.com/access-postgres: "true"
spec:
  ttlSecondsAfterFinished: 300
  template:
    metadata:
      labels:
        tracecat.com/access-postgres: "true"
    spec:
      restartPolicy: Never
      containers:
      - name: db-setup
        image: postgres:16
        env:
        - name: PGHOST
          value: "${aws_db_instance.tracecat.address}"
        - name: PGUSER
          value: "${var.rds_master_username}"
        - name: PGDATABASE
          value: "tracecat"
        - name: PGPASSWORD
          valueFrom:
            secretKeyRef:
              name: tracecat-postgres-credentials
              key: password
        command:
        - /bin/sh
        - -c
        - |
          psql -c "SELECT 1 FROM pg_database WHERE datname = 'temporal'" | grep -q 1 || psql -c "CREATE DATABASE temporal"
          psql -c "SELECT 1 FROM pg_database WHERE datname = 'temporal_visibility'" | grep -q 1 || psql -c "CREATE DATABASE temporal_visibility"
          echo "Temporal databases created successfully"
EOF

      echo "Waiting for temporal-db-setup job to complete..."
      if ! kubectl wait --for=condition=complete job/temporal-db-setup -n ${kubernetes_namespace.tracecat.metadata[0].name} --timeout=120s; then
        echo "temporal-db-setup job did not complete; dumping logs for debugging"
        kubectl logs job/temporal-db-setup -n ${kubernetes_namespace.tracecat.metadata[0].name} || true
        kubectl describe job/temporal-db-setup -n ${kubernetes_namespace.tracecat.metadata[0].name} || true
        exit 1
      fi
    EOT
  }

  depends_on = [
    aws_db_instance.tracecat,
    null_resource.tracecat_postgres_sg_policy,
    null_resource.postgres_credentials_external_secret,
    kubernetes_namespace.tracecat
  ]
}
