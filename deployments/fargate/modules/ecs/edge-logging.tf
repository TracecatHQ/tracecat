resource "aws_wafv2_web_acl_logging_configuration" "edge" {
  count = var.enable_waf && var.edge_log_bucket_name != null ? 1 : 0

  resource_arn            = aws_wafv2_web_acl.this[0].arn
  log_destination_configs = ["arn:aws:s3:::${var.edge_log_bucket_name}"]

  # No logging_filter: retain events for every action, including allowed requests.
  dynamic "redacted_fields" {
    for_each = toset(["authorization", "proxy-authorization", "cookie", "x-api-key"])
    content {
      single_header {
        name = redacted_fields.value
      }
    }
  }
}
