# WAF v2 Web ACL for ALB protection
# Gated by var.enable_waf - when enabled, the ACL ARN is passed to the ALB
# via the alb.ingress.kubernetes.io/wafv2-acl-arn ingress annotation.

# Regex pattern set for attachment upload endpoints (used by custom XSS/LFI exemption rules)
resource "aws_wafv2_regex_pattern_set" "attachments_endpoint" {
  count = var.enable_waf ? 1 : 0

  name        = "${var.cluster_name}-attachments-endpoint"
  description = "Matches case attachment upload endpoints"
  scope       = "REGIONAL"

  regular_expression {
    regex_string = "^/api/cases/[0-9a-f-]+/attachments$"
  }

  tags = var.tags
}

resource "aws_wafv2_web_acl" "main" {
  count = var.enable_waf ? 1 : 0

  name        = "${var.cluster_name}-waf"
  description = "WAF for Tracecat ALB - managed rule groups, rate limiting, and custom XSS/LFI exemptions"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  # Priority 0: Rate limiting - 2000 requests per 5 minutes per IP
  rule {
    name     = "RateLimitPerIP"
    priority = 0

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.cluster_name}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  # Priority 1: AWS Common Rule Set with count overrides for rules that need custom handling
  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"

        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }

        rule_action_override {
          name = "SizeRestrictions_QUERYSTRING"
          action_to_use {
            count {}
          }
        }

        rule_action_override {
          name = "CrossSiteScripting_BODY"
          action_to_use {
            count {}
          }
        }

        rule_action_override {
          name = "GenericLFI_BODY"
          action_to_use {
            count {}
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.cluster_name}-common-rules"
      sampled_requests_enabled   = true
    }
  }

  # Priority 2: Known Bad Inputs
  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.cluster_name}-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  # Priority 3: SQL Injection
  rule {
    name     = "AWSManagedRulesSQLiRuleSet"
    priority = 3

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.cluster_name}-sqli"
      sampled_requests_enabled   = true
    }
  }

  # Priority 4: Linux-specific rules
  rule {
    name     = "AWSManagedRulesLinuxRuleSet"
    priority = 4

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesLinuxRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.cluster_name}-linux-rules"
      sampled_requests_enabled   = true
    }
  }

  # Priority 5: Block XSS except on attachment upload endpoints
  # The CommonRuleSet sets CrossSiteScripting_BODY to count mode above,
  # and this rule re-blocks it unless the request is to an attachment endpoint.
  rule {
    name     = "BlockXSSExceptAttachments"
    priority = 5

    action {
      block {}
    }

    statement {
      and_statement {
        statement {
          label_match_statement {
            scope = "LABEL"
            key   = "awswaf:managed:aws:core-rule-set:CrossSiteScripting_BODY"
          }
        }

        statement {
          not_statement {
            statement {
              regex_pattern_set_reference_statement {
                arn = aws_wafv2_regex_pattern_set.attachments_endpoint[0].arn

                field_to_match {
                  uri_path {}
                }

                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.cluster_name}-xss-except-attachments"
      sampled_requests_enabled   = true
    }
  }

  # Priority 6: Block LFI except on attachment upload endpoints
  # Same pattern as XSS - CommonRuleSet GenericLFI_BODY is set to count,
  # and this rule re-blocks unless the request targets an attachment endpoint.
  rule {
    name     = "BlockLFIExceptAttachments"
    priority = 6

    action {
      block {}
    }

    statement {
      and_statement {
        statement {
          label_match_statement {
            scope = "LABEL"
            key   = "awswaf:managed:aws:core-rule-set:GenericLFI_BODY"
          }
        }

        statement {
          not_statement {
            statement {
              regex_pattern_set_reference_statement {
                arn = aws_wafv2_regex_pattern_set.attachments_endpoint[0].arn

                field_to_match {
                  uri_path {}
                }

                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.cluster_name}-lfi-except-attachments"
      sampled_requests_enabled   = true
    }
  }

  # Priority 7: IP Reputation List - blocks known bad IPs (botnets, scanners)
  rule {
    name     = "AWSManagedRulesAmazonIpReputationList"
    priority = 7

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.cluster_name}-ip-reputation"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.cluster_name}-waf"
    sampled_requests_enabled   = true
  }

  tags = var.tags
}
