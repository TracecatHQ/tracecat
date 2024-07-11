from aws_cdk import App

from .stack.alb import AlbStack
from .stack.fargate import FargateStack
from .stack.rds import RdsStack
from .stack.route53 import Route53Stack
from .stack.vpc import VpcStack

app = App()

vpc = VpcStack(scope=app, id="TracecatVpcStack")
route53 = Route53Stack(scope=app, id="TracecatRoute53Stack")
rds = RdsStack(scope=app, id="TracecatRdsStack")
fargate = FargateStack(
    scope=app,
    id="TracecatFargateStack",
    cluster=vpc.cluster,
    dns_namespace=vpc.dns_namespace,
    core_database=rds.core_database,
    core_security_group=vpc.core_security_group,
    temporal_database=rds.temporal_database,
    temporal_security_group=vpc.temporal_security_group,
)
alb = AlbStack(
    scope=app,
    id="TracecatAlbStack",
    cluster=vpc.cluster,
    hosted_zone=route53.hosted_zone,
    api_hosted_zone=route53.api_hosted_zone,
    certificate=route53.certificate,
    api_certificate=route53.api_certificate,
    ui_fargate_service=fargate.ui_fargate_service,
    api_fargate_service=fargate.api_fargate_service,
    ui_target_group=fargate.ui_target_group,
    api_target_group=fargate.api_target_group,
)
