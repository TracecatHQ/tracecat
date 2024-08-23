resource "aws_alb" "this" {
    name        = "tracecat-alb"
    subnets         = aws_subnet.public.*.id
    security_groups = [aws_security_group.ui_lb.id]
}

resource "aws_alb_target_group" "this" {
    name        = "tracecat-target-group"
    port        = 80 
    protocol    = "HTTP"
    vpc_id      = aws_vpc.tracecat.id
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

resource "aws_alb_listener" "this" {
  load_balancer_arn = aws_alb.this.id
  port              = "443"
  protocol          = "HTTPS"

  ssl_policy        = "ELBSecurityPolicy-2016-08"
  certificate_arn   = aws_acm_certificate.this.arn

  default_action {
    target_group_arn = aws_alb_target_group.tracecat_api.id
    type             = "forward"
  }
}
