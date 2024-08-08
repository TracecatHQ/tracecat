# random_pet suffix to prevent resource name clashing
resource "random_pet" "suffix" {
}

resource "random_password" "pw" {
  length = 15
}

resource "aws_secretsmanager_secret" "db_encryption_key" {
  name        = "DB_ENCRYPTION_KEY_NAME-${random_pet.suffix.id}"
  description = "Database encryption key for Tracecat"
}

resource "aws_secretsmanager_secret" "postgres_pwd" {
  name        = "POSTGRES_PWD-${random_pet.suffix.id}"
  description = "Postgres DB password"
}

resource "aws_secretsmanager_secret_version" "postgres_pwd" {
  secret_id     = aws_secretsmanager_secret.postgres_pwd.id
  secret_string = var.db_pass_value
  /*secret_string = jsonencode({
    password = random_password.pw.result
  })*/
}

data "aws_secretsmanager_secret_version" "postgres_pwd" {
  secret_id = aws_secretsmanager_secret.postgres_pwd.id
  version_id = aws_secretsmanager_secret_version.postgres_pwd.version_id
}


resource "aws_secretsmanager_secret_version" "db_encryption_key" {
  secret_id     = aws_secretsmanager_secret.db_encryption_key.id
  secret_string = var.db_encryption_key_value
}

resource "aws_secretsmanager_secret" "service_key" {
  name        = "SERVICE_KEY_NAME-${random_pet.suffix.id}"
  description = "Service key for Tracecat"
}

resource "aws_secretsmanager_secret_version" "service_key" {
  secret_id     = aws_secretsmanager_secret.service_key.id
  secret_string = var.service_key_value
}

resource "aws_secretsmanager_secret" "signing_secret" {
  name        = "SIGNING_SECRET_NAME-${random_pet.suffix.id}"
  description = "Signing secret for Tracecat"
}

resource "aws_secretsmanager_secret_version" "signing_secret" {
  secret_id     = aws_secretsmanager_secret.signing_secret.id
  secret_string = var.signing_secret_value
}

resource "aws_secretsmanager_secret" "db_pass" {
  name        = "DB_PASS_NAME-${random_pet.suffix.id}"
  description = "Database password for Tracecat"
}

resource "aws_secretsmanager_secret_version" "db_pass" {
  secret_id     = aws_secretsmanager_secret.db_pass.id
  secret_string = var.db_pass_value
}
