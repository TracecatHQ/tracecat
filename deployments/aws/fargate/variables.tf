locals {
  core_db_hostname = split(":", aws_db_instance.core_database.endpoint)[0]
  temp_db_hostname = split(":", aws_db_instance.temporal_database.endpoint)[0]
}

variable "az_count" {
    description = "Number of AZs to cover in a given region"
    default = "2"
}

variable "health_check_path" {
  default = "/"
}

variable "app_port" {
    description = "Port exposed by the docker image to redirect traffic to"
    default = 3000
}

locals {
  temporal_secrets = [ 
    {
      name  = "POSTGRES_PWD"
      valueFrom = aws_secretsmanager_secret.db_pass.id
    }
  ] 
  tracecat_ui_secrets = [  

  ]  
  tracecat_secrets = [    
    {
      name  = "TRACECAT__DB_ENCRYPTION_KEY"
      valueFrom = aws_secretsmanager_secret.db_encryption_key.id
    },
    {
      name  = "TRACECAT__SERVICE_KEY"
      valueFrom = aws_secretsmanager_secret.service_key.id 
    },
    {
      name  = "TRACECAT__SIGNING_SECRET"
      valueFrom = aws_secretsmanager_secret.signing_secret.id 
    },
    {
      name  = "TRACECAT__DB_PASS"
      valueFrom = aws_secretsmanager_secret.db_pass.id 
    },
  ]
  tracecat_ui_environment = [
    {
      name  = "CLERK_SECRET_KEY"
      value = "blahblah"
    },
    {
      name  = "CLERK_SECRET_KEY"
      value = "blahblah"
    },
    {
      name  = "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"
      value = "blahblah"
    },
    {
      name  = "NEXT_PUBLIC_DISABLE_AUTH"
      value = "true"
    },
    {
      name  = "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"
      value = "blahblah"
    },
    {
      name  = "NEXT_PUBLIC_DISABLE_AUTH"
      value = "true"
    },
    {
      name  = "NODE_ENV"
      value = "production"
    },
    {
      name  = "API_URL"
      value = "https://${var.cname_record_api}.${var.domain_name}" 
    },
    {
      name  = "NEXT_PUBLIC_API_URL"
      value = "https://${var.cname_record_api}.${var.domain_name}" 
    },
    {
      name  = "NEXT_PUBLIC_APP_ENV"
      value = "production"
    },
    {
      name  = "NEXT_PUBLIC_APP_URL"
      value = "https://${var.cname_record_app}.${var.domain_name}" 
    },
    {
      name  = "NEXT_SERVER_API_URL"
      value = "http://api-service:8000"
    },
  ]
  temporal_environment = [
    {
      name  = "BIND_ON_IP"
      value = "0.0.0.0" 
    },
    {
      name  = "TEMPORAL_BROADCAST_ADDRESS"
      value = "0.0.0.0"
    },
    {
      name  = "LOG_LEVEL"
      value = var.log_level
    },
    {
      name  = "POSTGRES_USER"
      value = "postgres"
    },
    {
      name  = "TEMPORAL__POSTGRES_USER"
      value = "postgres"
    },
    {
      name  = "TEMPORAL__POSTGRES_PASSWORD"
      value = var.db_pass_value
    },
    {
      name  = "DB"
      value = "postgres12"
    },
    {
      name  = "DB_PORT"
      value = "5432"
    },
    {
      name  = "POSTGRES_SEEDS"
      value = local.temp_db_hostname
    },
    {
      name  = "TEMPORAL__CLUSTER_URL"
      value = "temporal-service:7233"
      #value = "grpc://temporal-service:7233"
    },
    {
      name  = "TEMPORAL__CLUSTER_QUEUE"
      value = "tracecat-task-queue"
    },
    {
      name  = "TEMPORAL__CLUSTER_NAMESPACE"
      value = "tracecat-namespace"
    },
    {
      name  = "TEMPORAL__VERSION"
      value = "1.24.2"
    },
  ]
  tracecat_environment = [
    {
      name  = "TRACECAT__ALLOW_ORIGINS"
      value = "https://${var.cname_record_app}.${var.domain_name}" 
    },
    {
      name  = "LOG_LEVEL"
      value = var.log_level
    },
    {
      name  = "TRACECAT__API_URL"
      value = "https://${var.cname_record_api}.${var.domain_name}" 
    },
    {
      name  = "TRACECAT__APP_ENV"
      value = var.tracecat_app_env
    },
    {
      name  = "TRACECAT__DB_USER"
      value = "postgres"
    },
    {
      name  = "TRACECAT__DB_NAME"
      value = "postgres"
    },
    {
      name  = "TRACECAT__DB_ENDPOINT"
      value = local.core_db_hostname
    },
    {
      name  = "TRACECAT__DB_PORT"
      value = tostring(aws_db_instance.core_database.port)
    },
    {
      name  = "TRACECAT__DISABLE_AUTH"
      value = var.tracecat_disable_auth
    },
    {
      name  = "TRACECAT__PUBLIC_RUNNER_URL"
      value = "https://${var.cname_record_api}.${var.domain_name}" 
    },
    {
      name  = "TEMPORAL__CLUSTER_URL"
      #value = "grpc://temporal-service:7233"
      value = var.temporal_cluster_url
    },
    {
      name  = "TEMPORAL__CLUSTER_QUEUE"
      value = var.temporal_cluster_queue
    },
    {
      name  = "TRACECAT__DB_SSLMODE"
      value = "require"
    }
  ]
}

variable "log_level" {
  type    = string
  default = "INFO"
}

variable "tracecat_api_url" {
  type = string
  default = "https://blah"
}

variable "tracecat_app_env" {
  type = string
  default = "production"
}

variable "tracecat_disable_auth" {
  type = string
  default = "1"
}

variable "db_pass_value" {
  default = "Tracecat2024"
}

variable "temporal_cluster_url" {
  type = string
  default = "temporal-service:7233"
}

variable "signing_secret_value" {
  default = "bloah"
}

variable "service_key_value" {
  default = "blah"
}

variable "db_encryption_key_value" {
  default = "blah"
}

variable "temporal_cluster_queue" {
  type = string
  default = "tracecat-task-queue"
}
