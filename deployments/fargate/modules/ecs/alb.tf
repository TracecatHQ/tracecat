# Application Load Balancer
# Note: ALB is AWS-managed and does not expose Envoy tuning knobs; keep-alive
# behaviour for Service Connect SSE traffic must be handled in the downstream
# proxies (Caddy and the managed Envoy sidecars).
resource "aws_alb" "this" {
  name               = "${var.name_prefix}-alb"
  internal           = var.is_internal
  load_balancer_type = "application"
  subnets            = var.public_subnet_ids
  security_groups    = [aws_security_group.alb.id]

  dynamic "access_logs" {
    for_each = var.enable_alb_logging ? [var.alb_log_bucket_name] : []
    content {
      enabled = true
      bucket  = access_logs.value
      prefix  = "alb/access"
    }
  }

  dynamic "connection_logs" {
    for_each = var.enable_alb_logging ? [var.alb_log_bucket_name] : []
    content {
      enabled = true
      bucket  = connection_logs.value
      prefix  = "alb/connection"
    }
  }

  lifecycle {
    precondition {
      condition     = !var.enable_alb_logging || var.alb_log_bucket_name != null
      error_message = "Set alb_log_bucket_name to a same-region S3 bucket before enabling ALB logging."
    }
  }

  tags = {
    Name = "${var.name_prefix}-alb"
  }
}

# Target Group for Caddy
resource "aws_alb_target_group" "caddy" {
  name        = "${var.name_prefix}-caddy-tg"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    healthy_threshold   = "3"
    interval            = "30"
    protocol            = "HTTP"
    matcher             = "200"
    timeout             = "3"
    path                = "/"
    unhealthy_threshold = "2"
  }
}

# HTTPS Listener
resource "aws_alb_listener" "https" {
  load_balancer_arn = aws_alb.this.id
  port              = "443"
  protocol          = "HTTPS"

  ssl_policy      = var.alb_ssl_policy
  certificate_arn = var.acm_certificate_arn

  default_action {
    target_group_arn = aws_alb_target_group.caddy.id
    type             = "forward"
  }
}

# HTTP to HTTPS Redirect
resource "aws_alb_listener" "http" {
  load_balancer_arn = aws_alb.this.id
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}
