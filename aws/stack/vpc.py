from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from constructs import Construct


class VpcStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        ### Create VPC
        self.vpc = ec2.Vpc(self, "Vpc", vpc_name="tracecat-vpc")

        ### Create ECS cluster and internal DNS namespace
        self.cluster = ecs.Cluster(
            self, "Cluster", cluster_name="tracecat-ecs-cluster", vpc=self.vpc
        )
        self.dns_namespace = self.cluster.add_default_cloud_map_namespace(
            name="tracecat.local", vpc=self.vpc, use_for_service_connect=True
        )

        # Create security group for core tracecat services
        core_security_group = ec2.SecurityGroup(
            self,
            "CoreSecurityGroup",
            vpc=self.vpc,
            description="Security group for core Tracecat services",
        )
        core_security_group.add_ingress_rule(
            peer=core_security_group,
            connection=ec2.Port.tcp_range(8000, 8002),
            description="Allow internal communication between core Tracecat services",
        )

        # Create security grop for tracecat <-> temporal communication
        temporal_security_group = ec2.SecurityGroup(
            self,
            "TemporalSecurityGroup",
            vpc=self.vpc,
            description="Security group for communication between Tracecat and Temporal services",
        )
        temporal_security_group.add_ingress_rule(
            peer=temporal_security_group,
            connection=ec2.Port.tcp_range(8000, 8002),
            description="Allow internal communication between Tracecat and Temporal services",
        )
