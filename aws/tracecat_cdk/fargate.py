import os

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_servicediscovery as servicediscovery
from constructs import Construct

from .config import (
    API_DOMAIN_NAME,
    IS_PRODUCTION,
    TEMPORAL_SERVER_CPU,
    TEMPORAL_SERVER_IMAGE,
    TEMPORAL_SERVER_RAM,
    TRACECAT_API_CPU,
    TRACECAT_API_RAM,
    TRACECAT_IMAGE,
    TRACECAT_UI_CPU,
    TRACECAT_UI_IMAGE_TAG,
    TRACECAT_UI_RAM,
    TRACECAT_WORKER_CPU,
    TRACECAT_WORKER_RAM,
)


class FargateStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        cluster: ecs.Cluster,
        dns_namespace: servicediscovery.INamespace,
        api_hosted_zone: route53.IHostedZone,
        frontend_security_group: ec2.SecurityGroup,
        backend_security_group: ec2.SecurityGroup,
        core_database: rds.DatabaseInstance,
        core_db_secret: secretsmanager.Secret,
        core_db_security_group: ec2.SecurityGroup,
        temporal_database: rds.DatabaseInstance,
        temporal_db_secret: secretsmanager.Secret,
        temporal_db_security_group: ec2.SecurityGroup,
        **kwargs,
    ):
        super().__init__(scope, id, **kwargs)

        ### Internal ALB to route traffic to temporal service
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "FargateALB",
            vpc=cluster.vpc,
            http2_enabled=True,
            internet_facing=False,
            security_group=backend_security_group,
        )

        ### Tracecat API / Worker
        # Execution roles
        api_execution_role = iam.Role(
            self,
            "TracecatApiExecutionRole",
            role_name="TracecatApiFargateServiceExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        worker_execution_role = iam.Role(
            self,
            "TracecatWorkerExecutionRole",
            role_name="TracecatWorkerFargateServiceExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        ui_execution_role = iam.Role(
            self,
            "TracecatUiExecutionRole",
            role_name="TracecatUiFargateServiceExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        temporal_execution_role = iam.Role(
            self,
            "TemporalExecutionRole",
            role_name="TemporalFargateServiceExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        # Task roles
        api_task_role = iam.Role(
            self,
            "TracecatApiTaskRole",
            role_name="TracecatApiFargateServiceTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        worker_task_role = iam.Role(
            self,
            "TracecatWorkerTaskRole",
            role_name="TracecatWorkerFargateServiceTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        ui_task_role = iam.Role(
            self,
            "TracecatUiTaskRole",
            role_name="TracecatUiFargateServiceTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        temporal_task_role = iam.Role(
            self,
            "TemporalTaskRole",
            role_name="TemporalFargateServiceTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        ### Default execution role policy
        iam.Policy(
            self,
            "DefaultExecutionRolePolicy",
            statements=[
                # To pull image
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["ecs:Poll"],
                    resources=[
                        self.format_arn(resource="task-set/cluster/*", service="ecs")
                    ],
                ),
            ],
            roles=[
                api_execution_role,
                worker_execution_role,
                ui_execution_role,
                temporal_execution_role,
            ],
        )

        ### Gather secrets
        tracecat_secrets = {
            "TRACECAT__DB_ENCRYPTION_KEY": ecs.Secret.from_secrets_manager(
                secretsmanager.Secret.from_secret_partial_arn(
                    self,
                    "TracecatFullEncryptionKey",
                    secret_partial_arn=secretsmanager.Secret.from_secret_name_v2(
                        self,
                        "TracecatPartialEncryptionKey",
                        secret_name=os.environ["DB_ENCRYPTION_KEY_NAME"],
                    ).secret_arn,
                )
            ),
            "TRACECAT__SERVICE_KEY": ecs.Secret.from_secrets_manager(
                secretsmanager.Secret.from_secret_partial_arn(
                    self,
                    "TracecatFullServiceKey",
                    secret_partial_arn=secretsmanager.Secret.from_secret_name_v2(
                        self,
                        "TracecatPartialServiceKey",
                        secret_name=os.environ["SERVICE_KEY_NAME"],
                    ).secret_arn,
                )
            ),
            "TRACECAT__SIGNING_SECRET": ecs.Secret.from_secrets_manager(
                secretsmanager.Secret.from_secret_partial_arn(
                    self,
                    "TracecatFullSigningSecret",
                    secret_partial_arn=secretsmanager.Secret.from_secret_name_v2(
                        self,
                        "TracecatPartialSigningSecret",
                        secret_name=os.environ["SIGNING_SECRET_NAME"],
                    ).secret_arn,
                )
            ),
            "TRACECAT__DB_PASS": ecs.Secret.from_secrets_manager(secret=core_db_secret),
        }

        if os.getenv("CLERK_SECRET_KEY_NAME"):
            tracecat_ui_secrets = {
                "CLERK_SECRET_KEY": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_partial_arn(
                        self,
                        "ClerkFullSecretKey",
                        secret_partial_arn=secretsmanager.Secret.from_secret_name_v2(
                            self,
                            "ClerkPartialSecretKey",
                            secret_name=os.environ["CLERK_SECRET_KEY_NAME"],
                        ).secret_arn,
                    )
                ),
            }

        temporal_secrets = {
            "POSTGRES_PWD": ecs.Secret.from_secrets_manager(secret=temporal_db_secret)
        }

        ### Grant read access to secrets into env vars
        core_secrets_arns = [
            tracecat_secrets[secret].arn for secret in tracecat_secrets
        ]
        ui_secrets_arns = [
            tracecat_ui_secrets[secret].arn for secret in tracecat_ui_secrets
        ]
        api_execution_role.attach_inline_policy(
            policy=iam.Policy(
                self,
                "TracecatApiSecretsPolicy",
                statements=[
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW,
                        actions=["secretsmanager:GetSecretValue"],
                        resources=core_secrets_arns,
                    )
                ],
            )
        )
        worker_execution_role.attach_inline_policy(
            policy=iam.Policy(
                self,
                "TracecatWorkerSecretsPolicy",
                statements=[
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW,
                        actions=["secretsmanager:GetSecretValue"],
                        resources=core_secrets_arns,
                    )
                ],
            )
        )
        ui_execution_role.attach_inline_policy(
            policy=iam.Policy(
                self,
                "TracecatUiSecretsPolicy",
                statements=[
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW,
                        actions=["secretsmanager:GetSecretValue"],
                        resources=ui_secrets_arns,
                    )
                ],
            )
        )

        ### Log Group
        # NOTE: We share the log group across all services
        log_group = logs.LogGroup(
            self,
            "TracecatLogGroup",
            log_group_name="/ecs/tracecat",
            removal_policy=RemovalPolicy.DESTROY,
        )
        iam.Policy(
            self,
            "TracecatLogPolicy",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                    resources=[log_group.log_group_arn],
                )
            ],
            roles=[
                api_execution_role,
                worker_execution_role,
                ui_execution_role,
                temporal_execution_role,
            ],
        )

        # NOTE: Change the capacity according to your needs
        # We use spot instances by default to reduce costs
        capacity_provider_strategy = ecs.CapacityProviderStrategy(
            capacity_provider="FARGATE_SPOT", weight=1
        )

        ### Service Connect
        api_service_connect = ecs.ServiceConnectProps(
            services=[
                ecs.ServiceConnectService(
                    port_mapping_name="api",
                    dns_name="api-service",
                    port=8000,
                    idle_timeout=Duration.minutes(15),
                )
            ],
            namespace=dns_namespace.namespace_name,
            log_driver=ecs.LogDrivers.aws_logs(
                stream_prefix="service-connect-api", log_group=log_group
            ),
        )
        ui_service_connect = ecs.ServiceConnectProps(
            services=[
                ecs.ServiceConnectService(
                    port_mapping_name="ui",
                    dns_name="ui-service",
                    port=3000,
                    idle_timeout=Duration.minutes(15),
                )
            ],
            namespace=dns_namespace.namespace_name,
            log_driver=ecs.LogDrivers.aws_logs(
                stream_prefix="service-connect-ui", log_group=log_group
            ),
        )

        ### Shared API / worker environment
        tracecat_environment = {
            "LOG_LEVEL": os.environ["LOG_LEVEL"],
            "TRACECAT__API_URL": os.environ["TRACECAT__API_URL"],
            "TRACECAT__APP_ENV": os.environ["TRACECAT__APP_ENV"],
            "TRACECAT__DB_USER": "postgres",
            "TRACECAT__DB_NAME": "tracecat",
            "TRACECAT__DB_ENDPOINT": core_database.db_instance_endpoint_address,
            "TRACECAT__DB_PORT": core_database.db_instance_endpoint_port,
            "TRACECAT__DISABLE_AUTH": os.environ["TRACECAT__DISABLE_AUTH"],
            "TRACECAT__PUBLIC_RUNNER_URL": os.environ["TRACECAT__PUBLIC_RUNNER_URL"],
            "TEMPORAL__CLUSTER_URL": f"temporal.{API_DOMAIN_NAME}:443",
            "TEMPORAL__CLUSTER_QUEUE": os.environ["TEMPORAL__CLUSTER_QUEUE"],
        }

        ### API Service
        api_task_definition = ecs.FargateTaskDefinition(
            self,
            "TracecatApiTaskDefinition",
            cpu=TRACECAT_API_CPU,
            memory_limit_mib=TRACECAT_API_RAM,
            execution_role=api_execution_role,
            task_role=api_task_role,
        )
        api_task_definition.add_container(  # noqa
            "TracecatApiContainer",
            image=ecs.ContainerImage.from_registry(name=TRACECAT_IMAGE),
            environment={
                **tracecat_environment,
                "TRACECAT__ALLOW_ORIGINS": os.environ["TRACECAT__ALLOW_ORIGINS"],
                "CLERK_FRONTEND_API_URL": os.environ["CLERK_FRONTEND_API_URL"],
            },
            secrets=tracecat_secrets,
            port_mappings=[
                ecs.PortMapping(
                    container_port=8000,
                    name="api",
                    app_protocol=ecs.AppProtocol.http,
                )
            ],
            logging=ecs.LogDrivers.aws_logs(stream_prefix="api", log_group=log_group),
        )
        api_fargate_service = ecs.FargateService(
            self,
            "TracecatApiFargateService",
            cluster=cluster,
            service_name="tracecat-api",
            task_definition=api_task_definition,
            security_groups=[
                frontend_security_group,
                backend_security_group,
                core_db_security_group,
            ],
            service_connect_configuration=api_service_connect,
            capacity_provider_strategies=[capacity_provider_strategy],
        )
        self.api_fargate_service = api_fargate_service

        ### Worker Service
        worker_task_definition = ecs.FargateTaskDefinition(
            self,
            "TracecatWorkerTaskDefinition",
            cpu=TRACECAT_WORKER_CPU,
            memory_limit_mib=TRACECAT_WORKER_RAM,
            execution_role=worker_execution_role,
            task_role=worker_task_role,
        )
        worker_task_definition.add_container(  # noqa
            "TracecatWorkerContainer",
            image=ecs.ContainerImage.from_registry(name=TRACECAT_IMAGE),
            command=["python", "tracecat/dsl/worker.py"],
            environment=tracecat_environment,
            secrets=tracecat_secrets,
            port_mappings=[
                ecs.PortMapping(
                    container_port=8001,
                    name="worker",
                    app_protocol=ecs.AppProtocol.http,
                )
            ],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="worker", log_group=log_group
            ),
        )
        worker_fargate_service = ecs.FargateService(
            self,
            "TracecatWorkerFargateService",
            cluster=cluster,
            service_name="tracecat-worker",
            enable_execute_command=not IS_PRODUCTION,
            task_definition=worker_task_definition,
            security_groups=[backend_security_group, core_db_security_group],
            capacity_provider_strategies=[capacity_provider_strategy],
        )

        ### UI Service
        tracecat_ui_environment = {
            "NEXT_PUBLIC_API_URL": os.environ["NEXT_PUBLIC_API_URL"],
            "NEXT_PUBLIC_APP_ENV": os.environ["NEXT_PUBLIC_APP_ENV"],
            "NEXT_PUBLIC_APP_URL": os.environ["NEXT_PUBLIC_APP_URL"],
            "NEXT_PUBLIC_DISABLE_AUTH": os.environ["NEXT_PUBLIC_DISABLE_AUTH"],
            "NEXT_SERVER_API_URL": os.environ["NEXT_SERVER_API_URL"],
            "NODE_ENV": os.environ["NODE_ENV"],
        }
        if os.getenv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"):
            tracecat_ui_environment.update(
                {
                    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": os.environ[
                        "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"
                    ],
                    "NEXT_PUBLIC_CLERK_SIGN_IN_URL": os.environ[
                        "NEXT_PUBLIC_CLERK_SIGN_IN_URL"
                    ],
                    "NEXT_PUBLIC_CLERK_SIGN_OUT_URL": os.environ[
                        "NEXT_PUBLIC_CLERK_SIGN_OUT_URL"
                    ],
                }
            )
        if os.getenv("NEXT_PUBLIC_POSTHOG_KEY"):
            tracecat_ui_environment["NEXT_PUBLIC_POSTHOG_KEY"] = os.environ[
                "NEXT_PUBLIC_POSTHOG_KEY"
            ]

        ui_task_definition = ecs.FargateTaskDefinition(
            self,
            "TracecatUiTaskDefinition",
            cpu=TRACECAT_UI_CPU,
            memory_limit_mib=TRACECAT_UI_RAM,
            execution_role=ui_execution_role,
            task_role=ui_task_role,
        )
        ui_task_definition.add_container(  # noqa
            "TracecatUiContainer",
            image=ecs.ContainerImage.from_ecr_repository(
                repository=ecr.Repository.from_repository_name(
                    self, "TracecatUiRepository", repository_name="tracecat-ui"
                ),
                tag=TRACECAT_UI_IMAGE_TAG,
            ),
            environment=tracecat_ui_environment,
            secrets=tracecat_ui_secrets,
            port_mappings=[
                ecs.PortMapping(
                    container_port=3000,
                    name="ui",
                    app_protocol=ecs.AppProtocol.http,
                )
            ],
            logging=ecs.LogDrivers.aws_logs(stream_prefix="ui", log_group=log_group),
        )
        ui_fargate_service = ecs.FargateService(
            self,
            "TracecatUiFargateService",
            cluster=cluster,
            service_name="tracecat-ui",
            task_definition=ui_task_definition,
            security_groups=[frontend_security_group],
            service_connect_configuration=ui_service_connect,
            capacity_provider_strategies=[capacity_provider_strategy],
        )
        self.ui_fargate_service = ui_fargate_service

        ### Temporal Service
        temporal_task_definition = ecs.FargateTaskDefinition(
            self,
            "TemporalTaskDefinition",
            cpu=TEMPORAL_SERVER_CPU,
            memory_limit_mib=TEMPORAL_SERVER_RAM,
            execution_role=temporal_execution_role,
            task_role=temporal_task_role,
        )
        temporal_task_definition.add_container(
            "TemporalContainer",
            image=ecs.ContainerImage.from_registry(name=TEMPORAL_SERVER_IMAGE),
            secrets=temporal_secrets,
            environment={
                "LOG_LEVEL": os.environ["LOG_LEVEL"].lower(),
                "POSTGRES_USER": "postgres",
                "DB": "postgres12",  # Database driver for temporal
                "DB_PORT": "5432",
                "POSTGRES_SEEDS": temporal_database.db_instance_endpoint_address,
            },
            port_mappings=[
                ecs.PortMapping(
                    container_port=7233,
                    name="temporal",
                    app_protocol=ecs.AppProtocol.grpc,
                ),
            ],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="temporal", log_group=log_group
            ),
        )
        temporal_service = ecs.FargateService(
            self,
            "TemporalFargateService",
            cluster=cluster,
            service_name="temporal-server",
            enable_execute_command=not IS_PRODUCTION,
            task_definition=temporal_task_definition,
            security_groups=[
                backend_security_group,
                temporal_db_security_group,
            ],
            capacity_provider_strategies=[capacity_provider_strategy],
        )

        # Request a new certificate for temporal.api.<domain>
        temporal_certificate = acm.Certificate(
            self,
            "TemporalApiCertificate",
            domain_name=f"temporal.{API_DOMAIN_NAME}",
            validation=acm.CertificateValidation.from_dns(api_hosted_zone),
        )

        # Create a CNAME record for the Temporal subdomain
        route53.CnameRecord(
            self,
            "TemporalApiCnameRecord",
            zone=api_hosted_zone,
            record_name="temporal",
            domain_name=alb.load_balancer_dns_name,
        )

        # Create an A record for the Temporal subdomain
        temporal_a_record = route53.ARecord(
            self,
            "TemporalApiARecord",
            zone=api_hosted_zone,
            record_name="temporal",
            target=route53.RecordTarget.from_alias(targets.LoadBalancerTarget(alb)),
        )
        # Add dependency
        worker_fargate_service.node.add_dependency(temporal_a_record)

        # Temporal Target Group
        temporal_target_group = elbv2.ApplicationTargetGroup(
            self,
            "TemporalTargetGroup",
            port=7233,
            protocol=elbv2.ApplicationProtocol.HTTP,
            protocol_version=elbv2.ApplicationProtocolVersion.GRPC,
            targets=[temporal_service],
            health_check=elbv2.HealthCheck(enabled=True, healthy_grpc_codes="0-99"),
            vpc=alb.vpc,
        )

        # Listener with SSL certificate
        self.listener = alb.add_listener(
            "TemporalListener",
            port=443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            open=False,
            certificates=[temporal_certificate],
            default_target_groups=[temporal_target_group],
        )

        # Allow traffic to / from backend security group and the ALB
        backend_security_group.add_ingress_rule(
            peer=backend_security_group,
            connection=ec2.Port.tcp(443),
            description="Allow HTTPS traffic within the security group",
        )
        alb.connections.allow_from(
            backend_security_group,
            port_range=ec2.Port.tcp(443),
            description="Allow HTTPS traffic from the backend security group",
        )

        ### RDS Permissions

        # API
        core_database.connections.allow_default_port_from(
            api_fargate_service.connections
        )
        core_database.grant_connect(api_task_definition.task_role, db_user="postgres")

        # Worker
        core_database.connections.allow_default_port_from(
            worker_fargate_service.connections
        )
        core_database.grant_connect(
            worker_task_definition.task_role, db_user="postgres"
        )

        # Temporal
        temporal_database.connections.allow_default_port_from(
            temporal_service.connections
        )
        temporal_database.grant_connect(
            temporal_task_definition.task_role, db_user="postgres"
        )
