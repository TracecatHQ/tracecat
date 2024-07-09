from aws_cdk import Stack
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
        hosted_zone: route53.HostedZone | None = None,
        certificate: acm.Certificate | None = None,
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

        # Main HTTPS listener
        certificates = None
        if certificate is not None:
            certificates = [certificate]
        listener = alb.add_listener(
            "DefaultHttpsListener",
            port=443,
            certificates=certificates,
            default_action=elbv2.ListenerAction.fixed_response(404),
        )
        self.listener = listener

        # Point the domain to the load balancer
        if hosted_zone is not None:
            route53.ARecord(
                self,
                "AliasRecord",
                record_name=hosted_zone.zone_name,
                target=route53.RecordTarget.from_alias(LoadBalancerTarget(alb)),
                zone=hosted_zone,
            )

        # (Optional) Block all traffic except from the specified CIDR blocks
        for cidr_block in ALB_ALLOWED_CIDR_BLOCKS:
            alb.connections.allow_from(
                ec2.Peer.ipv4(cidr_block), ec2.Port.all_traffic()
            )
