import os

from aws_cdk import Duration, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_wafv2 as wafv2
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
            role_name="TracecatEngineExecutionRole",
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

        # Create task definition
        task_definition = ecs.FargateTaskDefinition(
            self, "TracecatEngineTaskDefinition", execution_role=execution_role
        )

        # Secrets
        tracecat_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "TracecatEngineSecret", secret_complete_arn=AWS_SECRET__ARN
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

        # Tracecat API
        api_container = task_definition.add_container(
            "TracecatApiContainer",
            image=ecs.ContainerImage.from_asset(
                directory=".",
                file="Dockerfile",
                build_args={"API_MODULE": "tracecat.api.app:app"},
            ),
            memory_limit_mib=512,
            environment={
                "API_MODULE": "tracecat.api.app:app",
                "SUPABASE_JWT_ALGORITHM": "HS256",
            },
            secrets=api_secrets,
        )
        api_container.add_port_mappings(ecs.PortMapping(container_port=8000))

        # Tracecat Runner
        runner_container = task_definition.add_container(
            "TracecatRunnerContainer",
            image=ecs.ContainerImage.from_asset(
                directory=".",
                file="Dockerfile",
                build_args={"API_MODULE": "tracecat.runner.app:app"},
            ),
            memory_limit_mib=512,
            environment={"API_MODULE": "tracecat.runner.app:app"},
            secrets=runner_secrets,
        )
        runner_container.add_port_mappings(ecs.PortMapping(container_port=8001))

        # Create Fargate service
        ecs_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "TracecatEngineALBFargateService",
            certificate=cert,
            cluster=cluster,
            desired_count=1,
            domain_zone=hosted_zone,
            health_check_grace_period=Duration.seconds(150),
            public_load_balancer=True,
            redirect_http=True,
            service_name="tracecat-fargate-fastapi",
            task_definition=task_definition,
        )

        # Add routing based on hostname or path with the single listern
        listener = ecs_service.load_balancer.listeners[0]
        listener.default_action = elbv2.ListenerAction.fixed_response(status_code=404)

        # API target
        listener.add_targets(
            "TracecatApiTarget",
            priority=10,
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            health_check=elbv2.HealthCheck(
                path="/api",
                enabled=True,
                interval=Duration.minutes(120),
                unhealthy_threshold_count=3,
                healthy_threshold_count=5,
                timeout=Duration.seconds(10),
            ),
            conditions=[
                elbv2.ListenerCondition.host_headers([AWS_ROUTE53__HOSTED_ZONE_NAME]),
                elbv2.ListenerCondition.path_patterns(["/api/*"]),
            ],
            targets=[
                ecs_service.service.load_balancer_target(
                    container_name="TracecatApiContainer", container_port=8000
                )
            ],
        )

        # Runner target
        listener.add_targets(
            "TracecatRunnerTarget",
            priority=20,
            port=8001,
            protocol=elbv2.ApplicationProtocol.HTTP,
            health_check=elbv2.HealthCheck(
                path="/runner",
                enabled=True,
                interval=Duration.minutes(120),
                unhealthy_threshold_count=3,
                healthy_threshold_count=5,
                timeout=Duration.seconds(10),
            ),
            conditions=[
                elbv2.ListenerCondition.host_headers([AWS_ROUTE53__HOSTED_ZONE_NAME]),
                elbv2.ListenerCondition.path_patterns(["/runner/*"]),
            ],
            targets=[
                ecs_service.service.load_balancer_target(
                    container_name="TracecatRunnerContainer", container_port=8001
                )
            ],
        )

        # Add WAF to block all traffic not from platform.tracecat.com
        # NOTE: Please change this to the domain you deployed Tracecat frontend to
        web_acl = wafv2.CfnWebACL(
            self,
            "TracecatWebAcl",
            scope="REGIONAL",
            # Block ALL requests by default
            default_action=wafv2.CfnWebACL.DefaultActionProperty(block={}),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="tracecat-webacl-metric",
                sampled_requests_enabled=True,
            ),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AllowSpecificDomainOverHttps",
                    priority=0,
                    action=wafv2.CfnWebACL.RuleActionProperty(allow={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        and_statement=wafv2.CfnWebACL.AndStatementProperty(
                            statements=[
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string=os.environ.get(
                                            "TRACECAT__UI_SUBDOMAIN",
                                            "platform.tracecat.com",
                                        ),
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header={"name": "Host"}
                                        ),
                                        positional_constraint="EXACTLY",
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(
                                                priority=0, type="LOWERCASE"
                                            )
                                        ],
                                    )
                                ),
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="https",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header={"name": "X-Forwarded-Proto"}
                                        ),
                                        positional_constraint="EXACTLY",
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(
                                                priority=0, type="NONE"
                                            )
                                        ],
                                    )
                                ),
                                wafv2.CfnWebACL.StatementProperty(
                                    or_statement=wafv2.CfnWebACL.OrStatementProperty(
                                        statements=[
                                            wafv2.CfnWebACL.StatementProperty(
                                                byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                                    search_string="/api/",
                                                    field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                                        single_query_argument={
                                                            "name": "uri"
                                                        }
                                                    ),
                                                    positional_constraint="STARTS_WITH",
                                                    text_transformations=[
                                                        wafv2.CfnWebACL.TextTransformationProperty(
                                                            priority=0,
                                                            type="URL_DECODE",
                                                        )
                                                    ],
                                                )
                                            ),
                                            wafv2.CfnWebACL.StatementProperty(
                                                byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                                    search_string="/runner/",
                                                    field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                                        single_query_argument={
                                                            "name": "uri"
                                                        }
                                                    ),
                                                    positional_constraint="STARTS_WITH",
                                                    text_transformations=[
                                                        wafv2.CfnWebACL.TextTransformationProperty(
                                                            priority=0,
                                                            type="URL_DECODE",
                                                        )
                                                    ],
                                                )
                                            ),
                                        ]
                                    )
                                ),
                            ]
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="allowSpecificDomainOverHttpsMetric",
                        sampled_requests_enabled=True,
                    ),
                )
            ],
        )

        # Associate the Web ACL with the ALB
        wafv2.CfnWebACLAssociation(
            self,
            "WebAclAssociation",
            resource_arn=ecs_service.load_balancer.load_balancer_arn,
            web_acl_arn=web_acl.attr_arn,
        )

        # Create A record to point the hosted zone domain to the ALB
        route53.ARecord(
            self,
            "AliasRecord",
            record_name=AWS_ROUTE53__HOSTED_ZONE_NAME,
            target=route53.RecordTarget.from_alias(
                LoadBalancerTarget(ecs_service.load_balancer)
            ),
            zone=hosted_zone,
        )
