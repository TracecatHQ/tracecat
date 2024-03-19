"""WARNING: the following code has an unresolved issue with multi-container deployments.
See https://github.com/aws/aws-cdk/issues/24013.

You must manually:
- Delete the silently added port mapping in the API container task definition
- Change the container name under fargate service load balancer to "RunnerContainer".
"""

import os

from aws_cdk import Duration, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_efs as efs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk.aws_certificatemanager import Certificate
from aws_cdk.aws_route53_targets import LoadBalancerTarget
from constructs import Construct

TRACECAT__APP_ENV = os.environ.get("TRACECAT__APP_ENV", "dev")
AWS_SECRET__ARN = os.environ["AWS_SECRET__ARN"]

AWS_ROUTE53__HOSTED_ZONE_ID = os.environ["AWS_ROUTE53__HOSTED_ZONE_ID"]
AWS_ROUTE53__HOSTED_ZONE_NAME = os.environ["AWS_ROUTE53__HOSTED_ZONE_NAME"]
AWS_ACM__CERTIFICATE_ARN = os.environ["AWS_ACM__CERTIFICATE_ARN"]


class TracecatEngineStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # Create cluster
        vpc = ec2.Vpc(self, "Vpc", vpc_name="tracecat-vpc")
        cluster = ecs.Cluster(
            self, "Cluster", cluster_name="tracecat-ecs-cluster", vpc=vpc
        )

        # Get hosted zone and certificate (created from AWS console)
        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self,
            "HostedZone",
            hosted_zone_id=AWS_ROUTE53__HOSTED_ZONE_ID,
            zone_name=AWS_ROUTE53__HOSTED_ZONE_NAME,
        )
        cert = Certificate.from_certificate_arn(
            self, "Certificate", AWS_ACM__CERTIFICATE_ARN
        )

        # Task execution IAM role (used across API and runner)
        execution_role = iam.Role(
            self,
            "ExecutionRole",
            role_name="TracecatFargateServiceExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        iam.Policy(
            self,
            "ExecutionRolePolicy",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                    resources=[
                        f"arn:aws:logs:{self.region}:{self.account}:log-group:/ecs/tracecat-*:*"
                    ],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:DescribeSecret",
                    ],
                    resources=[AWS_SECRET__ARN],
                ),
            ],
            roles=[execution_role],
        )

        # Secrets
        tracecat_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "Secret", secret_complete_arn=AWS_SECRET__ARN
        )
        api_secrets = {
            "TRACECAT__SIGNING_SECRET": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="signing-secret"
            ),
            "SUPABASE_JWT_SECRET": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="supabase-jwt-secret"
            ),
            "SUPABASE_PSQL_URL": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="supabase-psql-url"
            ),
            "OPENAI_API_KEY": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="openai-api-key"
            ),
        }
        runner_secrets = {
            "OPENAI_API_KEY": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="openai-api-key"
            )
        }

        # Define EFS
        file_system = efs.FileSystem(
            self,
            "FileSystem",
            vpc=vpc,
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            throughput_mode=efs.ThroughputMode.BURSTING,
        )

        # Create task definition
        task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDefinition",
            execution_role=execution_role,
            volumes=[
                ecs.Volume(
                    name="Volume",
                    efs_volume_configuration=ecs.EfsVolumeConfiguration(
                        file_system_id=file_system.file_system_id
                    ),
                )
            ],
        )

        # Tracecat API
        api_container = task_definition.add_container(
            "ApiContainer",
            image=ecs.ContainerImage.from_asset(
                directory=".",
                file="Dockerfile",
                build_args={"API_MODULE": "tracecat.api.app:app"},
            ),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8000/health"],
                interval=Duration.seconds(120),
                retries=5,
                start_period=Duration.seconds(60),
                timeout=Duration.seconds(10),
            ),
            memory_limit_mib=512,
            environment={
                "API_MODULE": "tracecat.api.app:app",
                "SUPABASE_JWT_ALGORITHM": "HS256",
            },
            secrets=api_secrets,
            port_mappings=[ecs.PortMapping(container_port=8000)],
        )
        api_container.add_mount_points(
            ecs.MountPoint(
                container_path="/home/apiuser/.tracecat",
                read_only=False,
                source_volume="Volume",
            )
        )

        # Tracecat Runner
        runner_container = task_definition.add_container(
            "RunnerContainer",
            image=ecs.ContainerImage.from_asset(
                directory=".",
                file="Dockerfile",
                build_args={"API_MODULE": "tracecat.runner.app:app", "PORT": "8001"},
            ),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8001/health"],
                interval=Duration.seconds(120),
                retries=5,
                start_period=Duration.seconds(60),
                timeout=Duration.seconds(10),
            ),
            memory_limit_mib=512,
            environment={"API_MODULE": "tracecat.runner.app:app", "PORT": "8001"},
            secrets=runner_secrets,
            port_mappings=[ecs.PortMapping(container_port=8001)],
        )
        runner_container.add_mount_points(
            ecs.MountPoint(
                container_path="/home/apiuser/.tracecat",
                read_only=False,
                source_volume="Volume",
            )
        )

        # Create ALB Fargate service
        ecs_service = ecs.FargateService(
            self,
            "FargateService",
            cluster=cluster,
            task_definition=task_definition,
        )

        service_health_check = elbv2.HealthCheck(
            enabled=True,
            interval=Duration.seconds(120),
            timeout=Duration.seconds(10),
            healthy_threshold_count=5,
            unhealthy_threshold_count=2,
            path="/health",
        )

        # Load balancer
        alb = elbv2.ApplicationLoadBalancer(
            self, "Alb", vpc=cluster.vpc, internet_facing=True, load_balancer_name="Alb"
        )

        # Main HTTPS listener
        listener = alb.add_listener("Listener", port=443, certificates=[cert])
        listener.add_action(
            "DefaultAction", action=elbv2.ListenerAction.fixed_response(status_code=200)
        )

        # Redirect HTTP to HTTPS
        alb.add_listener(
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

        listener.add_targets(
            "TargetGroup-ApiContainer",
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            priority=10,
            health_check=service_health_check,
            conditions=[elbv2.ListenerCondition.path_patterns(["/api", "/api/*"])],
            targets=[
                ecs_service.load_balancer_target(
                    container_name="ApiContainer", container_port=8000
                )
            ],
        )
        listener.add_targets(
            "TargetGroup-RunnerContainer",
            port=8001,
            protocol=elbv2.ApplicationProtocol.HTTP,
            priority=20,
            health_check=service_health_check,
            conditions=[
                elbv2.ListenerCondition.path_patterns(["/runner", "/runner/*"])
            ],
            targets=[
                ecs_service.load_balancer_target(
                    container_name="RunnerContainer", container_port=8001
                )
            ],
        )

        # Add WAFv2 WebACL to the ALB

        # # Define the IP set for VPC's IP range
        # private_cidr_blocks = [subnet.ipv4_cidr_block for subnet in vpc.private_subnets]
        # vpc_ip_set = wafv2.CfnIPSet(
        #     self,
        #     "VpcIpSet",
        #     addresses=private_cidr_blocks,
        #     scope="REGIONAL",
        #     ip_address_version="IPV4",
        # )

        # web_acl = wafv2.CfnWebACL(
        #     self,
        #     "WebAcl",
        #     scope="REGIONAL",
        #     # Block ALL requests by default
        #     default_action=wafv2.CfnWebACL.DefaultActionProperty(block={}),
        #     visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
        #         cloud_watch_metrics_enabled=True,
        #         metric_name="tracecatWebaclMetric",
        #         sampled_requests_enabled=True,
        #     ),
        #     rules=[
        #         # New rule for allowing health checks from within VPC
        #         wafv2.CfnWebACL.RuleProperty(
        #             name="AllowHealthChecks",
        #             priority=5,  # Set priority appropriately
        #             action=wafv2.CfnWebACL.RuleActionProperty(allow={}),
        #             statement=wafv2.CfnWebACL.StatementProperty(
        #                 ip_set_reference_statement=wafv2.CfnWebACL.IPSetReferenceStatementProperty(
        #                     arn=vpc_ip_set.attr_arn
        #                 )
        #             ),
        #             visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
        #                 cloud_watch_metrics_enabled=True,
        #                 metric_name="AllowHealthChecksMetric",
        #                 sampled_requests_enabled=True,
        #             ),
        #         ),
        #         # Block all traffic by default except for specific domain over HTTPS
        #         wafv2.CfnWebACL.RuleProperty(
        #             name="AllowSpecificDomainOverHttps",
        #             priority=10,
        #             action=wafv2.CfnWebACL.RuleActionProperty(allow={}),
        #             statement=wafv2.CfnWebACL.StatementProperty(
        #                 and_statement=wafv2.CfnWebACL.AndStatementProperty(
        #                     statements=[
        #                         wafv2.CfnWebACL.StatementProperty(
        #                             byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
        #                                 search_string=os.environ.get(
        #                                     "TRACECAT__UI_SUBDOMAIN",
        #                                     "platform.tracecat.com",
        #                                 ),
        #                                 field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
        #                                     single_header={"name": "Host"}
        #                                 ),
        #                                 positional_constraint="EXACTLY",
        #                                 text_transformations=[
        #                                     wafv2.CfnWebACL.TextTransformationProperty(
        #                                         priority=0, type="LOWERCASE"
        #                                     )
        #                                 ],
        #                             )
        #                         ),
        #                         wafv2.CfnWebACL.StatementProperty(
        #                             byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
        #                                 search_string="https",
        #                                 field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
        #                                     single_header={"name": "X-Forwarded-Proto"}
        #                                 ),
        #                                 positional_constraint="EXACTLY",
        #                                 text_transformations=[
        #                                     wafv2.CfnWebACL.TextTransformationProperty(
        #                                         priority=0, type="NONE"
        #                                     )
        #                                 ],
        #                             )
        #                         ),
        #                         wafv2.CfnWebACL.StatementProperty(
        #                             or_statement=wafv2.CfnWebACL.OrStatementProperty(
        #                                 statements=[
        #                                     wafv2.CfnWebACL.StatementProperty(
        #                                         byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
        #                                             search_string="/api/",
        #                                             field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
        #                                                 single_query_argument={
        #                                                     "name": "uri"
        #                                                 }
        #                                             ),
        #                                             positional_constraint="STARTS_WITH",
        #                                             text_transformations=[
        #                                                 wafv2.CfnWebACL.TextTransformationProperty(
        #                                                     priority=0,
        #                                                     type="URL_DECODE",
        #                                                 )
        #                                             ],
        #                                         )
        #                                     ),
        #                                     wafv2.CfnWebACL.StatementProperty(
        #                                         byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
        #                                             search_string="/runner/",
        #                                             field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
        #                                                 single_query_argument={
        #                                                     "name": "uri"
        #                                                 }
        #                                             ),
        #                                             positional_constraint="STARTS_WITH",
        #                                             text_transformations=[
        #                                                 wafv2.CfnWebACL.TextTransformationProperty(
        #                                                     priority=0,
        #                                                     type="URL_DECODE",
        #                                                 )
        #                                             ],
        #                                         )
        #                                     ),
        #                                 ]
        #                             )
        #                         ),
        #                     ]
        #                 )
        #             ),
        #             visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
        #                 cloud_watch_metrics_enabled=True,
        #                 metric_name="allowSpecificDomainOverHttpsMetric",
        #                 sampled_requests_enabled=True,
        #             ),
        #         ),
        #     ],
        # )

        # # Associate the Web ACL with the ALB
        # wafv2.CfnWebACLAssociation(
        #     self,
        #     "WebAclAssociation",
        #     resource_arn=alb.load_balancer_arn,
        #     web_acl_arn=web_acl.attr_arn,
        # )

        # Create A record to point the hosted zone domain to the ALB
        route53.ARecord(
            self,
            "AliasRecord",
            record_name=AWS_ROUTE53__HOSTED_ZONE_NAME,
            target=route53.RecordTarget.from_alias(LoadBalancerTarget(alb)),
            zone=hosted_zone,
        )
