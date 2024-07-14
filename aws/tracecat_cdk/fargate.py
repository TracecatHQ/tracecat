import os

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_servicediscovery as servicediscovery
from constructs import Construct

from .config import (
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
        core_database: rds.DatabaseInstance,
        core_db_secret: secretsmanager.Secret,
        core_security_group: ec2.SecurityGroup,
        core_db_security_group: ec2.SecurityGroup,
        temporal_database: rds.DatabaseInstance,
        temporal_db_secret: secretsmanager.Secret,
        temporal_security_group: ec2.SecurityGroup,
        temporal_db_security_group: ec2.SecurityGroup,
        **kwargs,
    ):
        super().__init__(scope, id, **kwargs)

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
        worker_service_connect = ecs.ServiceConnectProps(
            services=[
                ecs.ServiceConnectService(
                    port_mapping_name="worker",
                    dns_name="worker-service",
                    port=8001,
                    idle_timeout=Duration.minutes(15),
                )
            ],
            namespace=dns_namespace.namespace_name,
            log_driver=ecs.LogDrivers.aws_logs(
                stream_prefix="service-connect-worker", log_group=log_group
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
        temporal_service_connect = ecs.ServiceConnectProps(
            services=[
                ecs.ServiceConnectService(
                    port_mapping_name="temporal",
                    dns_name="temporal-service",
                    port=7233,
                    idle_timeout=Duration.minutes(15),
                )
            ],
            namespace=dns_namespace.namespace_name,
            log_driver=ecs.LogDrivers.aws_logs(
                stream_prefix="service-connect-temporal", log_group=log_group
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
            # Attach the security group to your ECS service
            task_definition=api_task_definition,
            security_groups=[
                core_security_group,
                temporal_security_group,
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
            # Attach the security group to your ECS service
            task_definition=worker_task_definition,
            security_groups=[
                core_security_group,
                core_db_security_group,
                temporal_security_group,
            ],
            service_connect_configuration=worker_service_connect,
            capacity_provider_strategies=[capacity_provider_strategy],
        )

        ### UI Service
        tracecat_ui_environment = {
            "NEXT_PUBLIC_API_URL": os.environ["NEXT_PUBLIC_API_URL"],
            "NEXT_PUBLIC_APP_ENV": os.environ["NEXT_PUBLIC_APP_ENV"],
            "NEXT_PUBLIC_APP_URL": os.environ["NEXT_PUBLIC_APP_URL"],
            "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": os.environ[
                "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"
            ],
            "NEXT_PUBLIC_CLERK_SIGN_IN_URL": os.environ[
                "NEXT_PUBLIC_CLERK_SIGN_IN_URL"
            ],
            "NEXT_PUBLIC_CLERK_SIGN_OUT_URL": os.environ[
                "NEXT_PUBLIC_CLERK_SIGN_OUT_URL"
            ],
            "NEXT_PUBLIC_DISABLE_AUTH": os.environ["NEXT_PUBLIC_DISABLE_AUTH"],
            "NEXT_PUBLIC_POSTHOG_KEY": os.environ["NEXT_PUBLIC_POSTHOG_KEY"],
            "NEXT_SERVER_API_URL": os.environ["NEXT_SERVER_API_URL"],
            "NODE_ENV": os.environ["NODE_ENV"],
        }
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
            # Attach the security group to your ECS service
            task_definition=ui_task_definition,
            security_groups=[core_security_group],
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
                    app_protocol=ecs.AppProtocol.http,
                )
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
            # Attach the security group to your ECS service
            task_definition=temporal_task_definition,
            security_groups=[temporal_security_group, temporal_db_security_group],
            service_connect_configuration=temporal_service_connect,
            capacity_provider_strategies=[capacity_provider_strategy],
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
