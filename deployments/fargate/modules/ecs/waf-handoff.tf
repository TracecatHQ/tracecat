# Preserve existing WAF resources for external ownership; see the WAF ownership section in ../../README.md.

removed {
  from = aws_wafv2_web_acl.this

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_wafv2_regex_pattern_set.attachments_endpoint

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_wafv2_regex_pattern_set.mcp_oauth_endpoints

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_wafv2_regex_pattern_set.mcp_public_endpoints

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_wafv2_web_acl_association.this

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_wafv2_web_acl_logging_configuration.edge

  lifecycle {
    destroy = false
  }
}
