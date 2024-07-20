from aws_cdk import Duration, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_route53 as route53
from aws_cdk.aws_route53_targets import LoadBalancerTarget
from constructs import Construct

from .config import ALB_ALLOWED_CIDR_BLOCKS


class AlbStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        cluster: ecs.Cluster,
        hosted_zone: route53.HostedZone,
        api_hosted_zone: route53.HostedZone,
        certificate: acm.Certificate,
        api_certificate: acm.Certificate,
        ui_fargate_service: ecs.FargateService,
        api_fargate_service: ecs.FargateService,
        frontend_security_group: ec2.SecurityGroup,
        **kwargs,
    ):
        super().__init__(scope, id, **kwargs)

        ### Load balancer
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "TracecatEngineAlb",
            vpc=cluster.vpc,
            internet_facing=True,
            load_balancer_name="tracecat-engine-alb",
            security_group=frontend_security_group,
        )
        alb.add_listener(
            # Redirect HTTP to HTTPS
            "HttpListener",
            port=80,
            default_action=elbv2.ListenerAction.redirect(
                port="443",
                protocol="HTTPS",
                host="#{host}",
                path="/#{path}",
                query="#{query}",
                permanent=True,
            ),
        )

        listener = alb.add_listener(
            "DefaultHttpsListener",
            port=443,
            certificates=[certificate, api_certificate],
            default_action=elbv2.ListenerAction.fixed_response(404),
        )

        # Add targets
        ui_target_group = elbv2.ApplicationTargetGroup(
            self,
            "TracecatUiTargetGroup",
            target_type=elbv2.TargetType.IP,
            port=3000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            vpc=cluster.vpc,
        )
        ui_target_group.add_target(
            ui_fargate_service.load_balancer_target(
                container_name="TracecatUiContainer", container_port=3000
            )
        )

        api_target_group = elbv2.ApplicationTargetGroup(
            self,
            "TracecatApiTargetGroup",
            target_type=elbv2.TargetType.IP,
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            vpc=cluster.vpc,
            health_check=elbv2.HealthCheck(
                path="/health",
                interval=Duration.seconds(120),
                timeout=Duration.seconds(10),
                healthy_threshold_count=5,
                unhealthy_threshold_count=2,
            ),
        )
        api_target_group.add_target(
            api_fargate_service.load_balancer_target(
                container_name="TracecatApiContainer", container_port=8000
            )
        )

        # Add action to forward traffic to the UI service
        listener.add_action(
            "RootListenerAction",
            priority=10,
            conditions=[elbv2.ListenerCondition.host_headers([hosted_zone.zone_name])],
            action=elbv2.ListenerAction.forward(target_groups=[ui_target_group]),
        )

        listener.add_action(
            "ApiListenerAction",
            priority=20,
            conditions=[
                elbv2.ListenerCondition.host_headers([api_hosted_zone.zone_name])
            ],
            action=elbv2.ListenerAction.forward(target_groups=[api_target_group]),
        )

        self.listener = listener

        # Create A record to point hosted zone to ALB
        route53.ARecord(
            self,
            "UiAliasRecord",
            record_name=f"{hosted_zone.zone_name}.",
            target=route53.RecordTarget.from_alias(LoadBalancerTarget(alb)),
            zone=hosted_zone,
        )

        # Create A record to point API hosted zone to ALB
        route53.ARecord(
            self,
            "ApiAliasRecord",
            record_name=f"{api_hosted_zone.zone_name}.",
            target=route53.RecordTarget.from_alias(LoadBalancerTarget(alb)),
            zone=api_hosted_zone,
        )

        # (Optional) Block all traffic except from the specified CIDR blocks
        for cidr_block in ALB_ALLOWED_CIDR_BLOCKS:
            alb.connections.allow_from(
                ec2.Peer.ipv4(cidr_block), ec2.Port.all_traffic()
            )
