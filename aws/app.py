import os

from aws_cdk import App
from tracecat_cdk.alb import AlbStack
from tracecat_cdk.fargate import FargateStack
from tracecat_cdk.rds import RdsStack
from tracecat_cdk.route53 import Route53Stack
from tracecat_cdk.vpc import VpcStack

app = App()
env = {
    "account": os.environ["AWS_DEFAULT_ACCOUNT"],
    "region": os.environ["AWS_DEFAULT_REGION"],
}

vpc = VpcStack(scope=app, id="TracecatVpcStack", env=env)
route53 = Route53Stack(scope=app, id="TracecatRoute53Stack", env=env)
rds = RdsStack(
    scope=app,
    id="TracecatRdsStack",
    vpc=vpc.vpc,
    env=env,
)
fargate = FargateStack(
    scope=app,
    id="TracecatFargateStack",
    cluster=vpc.cluster,
    dns_namespace=vpc.dns_namespace,
    api_hosted_zone=route53.api_hosted_zone,
    frontend_security_group=vpc.frontend_security_group,
    backend_security_group=vpc.backend_security_group,
    core_database=rds.core_database,
    core_db_secret=rds.core_db_secret,
    core_db_security_group=rds.core_db_security_group,
    temporal_database=rds.temporal_database,
    temporal_db_secret=rds.temporal_db_secret,
    temporal_db_security_group=rds.temporal_db_security_group,
    env=env,
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
    frontend_security_group=vpc.frontend_security_group,
    env=env,
)

app.synth()
