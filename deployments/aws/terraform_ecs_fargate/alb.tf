resource "aws_alb" "tracecat_ui" {
    name        = "ui-load-balancer"
    subnets         = aws_subnet.public.*.id
    security_groups = [aws_security_group.ui_lb.id]
}

resource "aws_alb" "tracecat_api" {
    name        = "api-load-balancer"
    subnets         = aws_subnet.public.*.id
    security_groups = [aws_security_group.api_lb.id]
}

resource "aws_alb_target_group" "tracecat_ui" {
    name        = "ui-target-group"
    port        = 3000 
    protocol    = "HTTP"
    vpc_id      = aws_vpc.tracecat.id
    target_type = "ip"

    health_check {
        healthy_threshold   = "3"
        interval            = "30"
        protocol            = "HTTP"
        matcher             = "200"
        timeout             = "3"
        path                = var.health_check_path
        unhealthy_threshold = "2"
    }
}

resource "aws_alb_target_group" "tracecat_api" {
    name        = "api-target-group"
    port        = 8000
    protocol    = "HTTP"
    vpc_id      = aws_vpc.tracecat.id
    target_type = "ip"

    health_check {
        healthy_threshold   = "3"
        interval            = "30"
        protocol            = "HTTP"
        matcher             = "200"
        timeout             = "3"
        path                = var.health_check_path
        unhealthy_threshold = "2"
    }
}
resource "aws_alb_listener" "tracecat_ui" {
  load_balancer_arn = aws_alb.tracecat_ui.id
  port              = "443" 
  protocol          = "HTTPS"

  ssl_policy        = "ELBSecurityPolicy-2016-08"
  certificate_arn   = aws_acm_certificate.cert_app.arn

  default_action {
    target_group_arn = aws_alb_target_group.tracecat_ui.id
    type             = "forward"
  }
}

resource "aws_alb_listener" "tracecat_api" {
  load_balancer_arn = aws_alb.tracecat_api.id
  port              = "443"
  protocol          = "HTTPS"

  ssl_policy        = "ELBSecurityPolicy-2016-08"
  certificate_arn   = aws_acm_certificate.cert_api.arn

  default_action {
    target_group_arn = aws_alb_target_group.tracecat_api.id
    type             = "forward"
  }
}
