from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from constructs import Construct


class VpcStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        # Create VPC
        vpc = ec2.Vpc(self, "Vpc", vpc_name="tracecat-vpc")
        self.vpc = vpc

        # Create ECS cluster and internal DNS namespace
        self.cluster = ecs.Cluster(
            self, "Cluster", cluster_name="tracecat-ecs-cluster", vpc=vpc
        )
        self.dns_namespace = self.cluster.add_default_cloud_map_namespace(
            name="tracecat.local", vpc=vpc, use_for_service_connect=True
        )

        # Create security group for core Tracecat services
        core_security_group = ec2.SecurityGroup(
            self,
            "CoreSecurityGroup",
            vpc=vpc,
            description="Security group for core Tracecat services",
        )

        # API to UI (frontend) communication
        frontend_security_group.add_ingress_rule(
            peer=core_security_group,
            connection=ec2.Port.tcp(3000),
            description="Allow internal traffic from Tracecat UI",
        )
        frontend_security_group.add_ingress_rule(
            peer=core_security_group,
            connection=ec2.Port.tcp(8000),
            description="Allow internal traffic from Tracecat API",
        )

        # Security group for API, worker, and temporal server
        backend_security_group = ec2.SecurityGroup(
            self,
            "BackendSecurityGroup",
            vpc=vpc,
            description="Security group for Temporal worker services",
        )
        backend_security_group.add_ingress_rule(
            peer=backend_security_group,
            connection=ec2.Port.tcp(7233),
            description="Allow traffic from Temporal server",
        )
        backend_security_group.add_ingress_rule(
            peer=backend_security_group,
            connection=ec2.Port.tcp(8000),
            description="Allow traffic from core Tracecat services (API and worker)",
        )

        self.frontend_security_group = frontend_security_group
        self.backend_security_group = backend_security_group
