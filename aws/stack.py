import os

from aws_cdk import Duration, Stack, route53
from aws_cdk import aws_acm as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_iam as iam
from aws_cdk import aws_wafv2 as wafv2
from aws_cdk.aws_secretsmanager import Secret
from constructs import Construct

TRACECAT__APP_ENV = os.environ.get("TRACECAT__APP_ENV", "dev")
SECRET_NAME_PREFIX = f"tracecat/{TRACECAT__APP_ENV}"

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
        cert = acm.Certificate.from_certificate_arn(
            self, "Certificate", AWS_ACM__CERTIFICATE_ARN
        )

        # Secrets
        tracecat_signing_secret = Secret.from_secret_name_v2(
            self, "ApiSigningSecret", secret_name="tracecat/signing-secret"
        )
        supabase_jwt_secret = Secret.from_secret_name_v2(
            self,
            "SupabaseJwtSecret",
            secret_name=f"{SECRET_NAME_PREFIX}/supabase-jwt-secret",
        )
        psql_url_secret = Secret.from_secret_name_v2(
            self, "SupabasePsqlUrl", f"{SECRET_NAME_PREFIX}/supabase-psql-url"
        )
        openai_api_key = Secret.from_secret_name_v2(
            self, "OpenAIApiKey", secret_name=f"{SECRET_NAME_PREFIX}/openai-api-key"
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
                    resources=[
                        tracecat_signing_secret.secret_arn,
                        supabase_jwt_secret.secret_arn,
                        psql_url_secret.secret_arn,
                        openai_api_key.secret_arn,
                    ],
                ),
            ],
            roles=[execution_role],
        )

        # Create task definition
        task_definition = ecs.FargateTaskDefinition(
            self, "TracecatEngineTaskDefinition", execution_role=execution_role
        )

        # Secrets
        api_secrets = {
            "TRACECAT__SIGNING_SECRET": ecs.Secret.from_secrets_manager(
                tracecat_signing_secret
            ),
            "SUPABASE_JWT_SECRET": ecs.Secret.from_secrets_manager(supabase_jwt_secret),
            "SUPABASE_PSQL_URL": ecs.Secret.from_secrets_manager(psql_url_secret),
            "OPENAI_API_KEY": ecs.Secret.from_secrets_manager(openai_api_key),
        }
        runner_secrets = {
            "OPENAI_API_KEY": ecs.Secret.from_secrets_manager(openai_api_key)
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
        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "TracecatEngineALBFargateService",
            cluster=cluster,
            desired_count=1,
            domain_zone=hosted_zone,
            health_check_grace_period=Duration.seconds(150),
            public_load_balancer=True,
            redirect_http=True,
            service_name="tracecat-fargate-fastapi",
            task_definition=task_definition,
            certificate=cert,
        )

        # Add routing based on hostname or path
        listener = service.load_balancer.add_listener(
            "Listener",
            port=443,
            certificates=[cert],  # Define your certificate
        )

        # API target
        listener.add_targets(
            "ApiTarget",
            priority=10,
            path_pattern="/api/*",
            targets=[
                service.service.load_balancer_target(
                    container_name="ApiContainer", container_port=8000
                )
            ],
        )

        # Runner target
        listener.add_targets(
            "RunnerTarget",
            priority=20,
            path_pattern="/runner/*",
            targets=[
                service.service.load_balancer_target(
                    container_name="RunnerContainer", container_port=8001
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
                                    )
                                ),
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="443",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header={"name": "X-Forwarded-Port"}
                                        ),
                                        positional_constraint="EXACTLY",
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
            resource_arn=service.load_balancer.load_balancer_arn,
            web_acl_arn=web_acl.attr_arn,
        )

        # Retrieve the target group
        target_group = self.ecs_service.target_group
        # Change the success codes
        target_group.configure_health_check(path="/", healthy_http_codes="200")
