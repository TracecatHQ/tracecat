from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from constructs import Construct


class VpcStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
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

        # Tracecat rules
        core_security_group.add_ingress_rule(
            peer=core_security_group,
            connection=ec2.Port.tcp(8000),
            description="Allow internal traffic to the Tracecat API service on port 8000",
        )
        core_security_group.add_ingress_rule(
            peer=core_security_group,
            connection=ec2.Port.tcp(8001),
            description="Allow internal traffic to the Tracecat Worker service on port 8001",
        )
        core_security_group.add_ingress_rule(
            peer=core_security_group,
            connection=ec2.Port.tcp(3000),
            description="Allow internal traffic to the Tracecat UI service on port 3000",
        )
        core_security_group.add_ingress_rule(
            peer=core_security_group,
            connection=ec2.Port.tcp(5432),
            description="Allow internal traffic to the Tracecat RDS instance on port 5432",
        )

        # Create security group for Temporal services
        temporal_security_group = ec2.SecurityGroup(
            self,
            "TemporalSecurityGroup",
            vpc=vpc,
            description="Security group for Temporal services",
        )
        # Temporal rules
        temporal_security_group.add_ingress_rule(
            peer=temporal_security_group,
            connection=ec2.Port.tcp(8080),
            description="Allow internal traffic to the Temporal UI service on port 8080",
        )
        temporal_security_group.add_ingress_rule(
            peer=temporal_security_group,
            connection=ec2.Port.tcp(7233),
            description="Allow internal traffic to the Temporal server on port 7233",
        )
        temporal_security_group.add_ingress_rule(
            peer=temporal_security_group,
            connection=ec2.Port.tcp(5432),
            description="Allow internal traffic to the Temporal RDS instance on port 5432",
        )
        temporal_security_group.add_ingress_rule(
            peer=temporal_security_group,
            connection=ec2.Port.tcp(8000),
            description="Allow internal traffic from Tracecat API service on port 8000",
        )
        temporal_security_group.add_ingress_rule(
            peer=temporal_security_group,
            connection=ec2.Port.tcp(8001),
            description="Allow internal traffic from Tracecat Worker service on port 8001",
        )
