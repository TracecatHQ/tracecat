resource "aws_cloudwatch_log_group" "tracecat_log_group" {
  name              = "/ecs/tracecat"
  retention_in_days = 30
}
