import os

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
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
    TEMPORAL_UI_CPU,
    TEMPORAL_UI_IMAGE,
    TEMPORAL_UI_RAM,
    TRACECAT_API_CPU,
    TRACECAT_API_RAM,
    TRACECAT_IMAGE,
    TRACECAT_UI_CPU,
    TRACECAT_UI_IMAGE,
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
        core_security_group: ec2.SecurityGroup,
        temporal_database: rds.DatabaseInstance,
        temporal_security_group: ec2.SecurityGroup,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        ### Gather secrets
        tracecat_secrets = {
            "TRACECAT__DB_ENCRYPTION_KEY": ecs.Secret.from_secrets_manager(
                secretsmanager.Secret.from_secret_partial_arn(
                    self,
                    "TracecatDbEncryptionKey",
                    secret_partial_arn=secretsmanager.Secret.from_secret_name_v2(
                        self,
                        "TracecatPartialDbEncryptionKey",
                        secret_name=os.environ["DB_ENCRYPTION_KEY_NAME"],
                    ).secret_arn,
                )
            ),
            "TRACECAT__SERVICE_KEY": ecs.Secret.from_secrets_manager(
                secretsmanager.Secret.from_secret_partial_arn(
                    self,
                    "TracecatServiceKey",
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
                    "TracecatSigningKey",
                    secret_partial_arn=secretsmanager.Secret.from_secret_name_v2(
                        self,
                        "TracecatPartialSigningKey",
                        secret_name=os.environ["SIGNING_SECRET_NAME"],
                    ).secret_arn,
                )
            ),
        }

        tracecat_ui_secrets = {
            "CLERK_SECRET_KEY": ecs.Secret.from_secrets_manager(
                secretsmanager.Secret.from_secret_partial_arn(
                    self,
                    "ClerkSecretKey",
                    secret_partial_arn=secretsmanager.Secret.from_secret_name_v2(
                        self,
                        "ClerkPartialSecretKey",
                        secret_name=os.environ["CLERK_SECRET_KEY_NAME"],
                    ).secret_arn,
                )
            ),
        }

        ### IAM: Task execution IAM role
        execution_role = iam.Role(
            self,
            "ExecutionRole",
            role_name="TracecatFargateServiceExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        logs_group_pattern = (
            f"arn:aws:logs:{self.region}:{self.account}:" "log-group:/ecs/tracecat*:*"
        )
        iam.Policy(
            self,
            "ExecutionRolePolicy",
            statements=[
                # For logging
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                    resources=[logs_group_pattern],
                ),
                # To pull image
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["ecs:Poll"],
                    resources=[
                        self.format_arn(resource="task-set/cluster/*", service="ecs")
                    ],
                ),
            ],
            roles=[execution_role],
        )

        # Task role
        task_role = iam.Role(
            self,
            "TaskRole",
            role_name="TracecatTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        ### Log Group
        log_group = logs.LogGroup(
            self,
            "TracecatLogGroup",
            log_group_name="/ecs/tracecat",
            removal_policy=RemovalPolicy.DESTROY,
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
                stream_prefix="tracecat-api-service-connect", log_group=log_group
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
                stream_prefix="tracecat-worker-service-connect", log_group=log_group
            ),
        )

        ### Shared API / worker environment
        tracecat_environment = {
            "LOG_LEVEL": os.environ["LOG_LEVEL"],
            "TRACECAT__API_URL": os.environ["TRACECAT__API_URL"],
            "TRACECAT__APP_ENV": os.environ["TRACECAT__APP_ENV"],
            "TRACECAT__DB_URI": core_database.db_instance_endpoint_address,
            "TRACECAT__DISABLE_AUTH": os.environ["TRACECAT__DISABLE_AUTH"],
            "TRACECAT__PUBLIC_RUNNER_URL": os.environ["TRACECAT__PUBLIC_RUNNER_URL"],
        }

        ### API Service
        api_task_definition = ecs.FargateTaskDefinition(
            self,
            "ApiTaskDefinition",
            cpu=TRACECAT_API_CPU,
            memory_limit_mib=TRACECAT_API_RAM,
            execution_role=execution_role,
            task_role=task_role,
        )
        api_task_definition.add_container(  # noqa
            "ApiContainer",
            image=ecs.ContainerImage.from_registry(name=TRACECAT_IMAGE),
            environment={
                **tracecat_environment,
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
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="tracecat-api", log_group=log_group
            ),
        )
        api_fargate_service = ecs.FargateService(
            self,
            "TracecatApiFargateService",
            cluster=cluster,
            service_name="tracecat-api",
            # Attach the security group to your ECS service
            task_definition=api_task_definition,
            security_groups=[core_security_group, temporal_security_group],
            service_connect_configuration=api_service_connect,
            capacity_provider_strategies=[capacity_provider_strategy],
        )
        self.api_fargate_service = api_fargate_service

        ### Worker Service
        worker_task_definition = ecs.FargateTaskDefinition(
            self,
            "WorkerTaskDefinition",
            cpu=TRACECAT_WORKER_CPU,
            memory_limit_mib=TRACECAT_WORKER_RAM,
            execution_role=execution_role,
            task_role=task_role,
        )
        worker_task_definition.add_container(  # noqa
            "WorkerContainer",
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
                stream_prefix="tracecat-worker", log_group=log_group
            ),
        )
        ecs.FargateService(
            self,
            "TracecatWorkerFargateService",
            cluster=cluster,
            service_name="tracecat-worker",
            # Attach the security group to your ECS service
            task_definition=worker_task_definition,
            security_groups=[core_security_group, temporal_security_group],
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
            "NEXT_PUBLIC_CLERK_SIGN_IN_URL": os.environ["CLERK_SIGN_IN_URL"],
            "NEXT_PUBLIC_CLERK_SIGN_UP_URL": os.environ["CLERK_SIGN_UP_URL"],
            "NEXT_PUBLIC_DISABLE_AUTH": os.environ["NEXT_PUBLIC_DISABLE_AUTH"],
            "NEXT_SERVER_API_URL": os.environ["NEXT_SERVER_API_URL"],
            "NODE_ENV": os.environ["TRACECAT__APP_ENV"],
        }
        ui_task_definition = ecs.FargateTaskDefinition(
            self,
            "TracecatUiTaskDefinition",
            cpu=TRACECAT_UI_CPU,
            memory_limit_mib=TRACECAT_UI_RAM,
            execution_role=execution_role,
            task_role=task_role,
        )
        ui_task_definition.add_container(  # noqa
            "TracecatUiContainer",
            image=ecs.ContainerImage.from_registry(name=TRACECAT_UI_IMAGE),
            environment=tracecat_ui_environment,
            ui_secrets=tracecat_ui_secrets,
            port_mappings=[
                ecs.PortMapping(
                    container_port=3000,
                    name="ui",
                    app_protocol=ecs.AppProtocol.http,
                )
            ],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="tracecat-ui", log_group=log_group
            ),
        )
        ui_fargate_service = ecs.FargateService(
            self,
            "TracecatUiFargateService",
            cluster=cluster,
            service_name="tracecat-ui",
            # Attach the security group to your ECS service
            task_definition=ui_task_definition,
            security_groups=[core_security_group],
            capacity_provider_strategies=[capacity_provider_strategy],
        )
        self.ui_fargate_service = ui_fargate_service

        ### Temporal Service
        temporal_task_definition = ecs.FargateTaskDefinition(
            self,
            "TemporalTaskDefinition",
            cpu=TEMPORAL_SERVER_CPU,
            memory_limit_mib=TEMPORAL_SERVER_RAM,
            execution_role=execution_role,
            task_role=task_role,
        )
        temporal_task_definition.add_container(
            "TemporalContainer",
            image=ecs.ContainerImage.from_registry(name=TEMPORAL_SERVER_IMAGE),
            environment={
                "DB": "postgres12",
                "DB_PORT": "5432",
                "POSTGRES_USER": temporal_database.secret.secret_value_from_json(
                    "username"
                ).to_string(),
                "POSTGRES_PWD": temporal_database.secret.secret_value_from_json(
                    "password"
                ).to_string(),
                "POSTGRES_SEEDS": temporal_database.db_instance_endpoint_address,
            },
        )
        ecs.FargateService(
            self,
            "TemporalFargateService",
            cluster=cluster,
            service_name="temporal-server",
            # Attach the security group to your ECS service
            task_definition=temporal_task_definition,
            security_groups=[temporal_security_group],
            capacity_provider_strategies=[capacity_provider_strategy],
        )

        ### Temporal UI
        temporal_ui_task_definition = ecs.FargateTaskDefinition(
            self,
            "TemporalUiTaskDefinition",
            cpu=TEMPORAL_UI_CPU,
            memory_limit_mib=TEMPORAL_UI_RAM,
            execution_role=execution_role,
            task_role=task_role,
        )
        temporal_ui_task_definition.add_container(
            "TemporalUiContainer",
            image=ecs.ContainerImage.from_registry(name=TEMPORAL_UI_IMAGE),
            environment={
                "TEMPORAL_ADDRESS": "http://temporal-server-service:7233",
                "TEMPORAL_CORS_ORIGIN": "http://temporal-ui-service:8080",
            },
        )
        ecs.FargateService(
            self,
            "TemporalUiFargateService",
            cluster=cluster,
            service_name="temporal-ui",
            # Attach the security group to your ECS service
            task_definition=temporal_ui_task_definition,
            security_groups=[temporal_security_group],
            capacity_provider_strategies=[capacity_provider_strategy],
        )
