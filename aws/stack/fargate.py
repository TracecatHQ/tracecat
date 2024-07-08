import os

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from .config import TEMPORAL_IMAGE, TEMPORAL_UI_IMAGE, TRACECAT_IMAGE, TRACECAT_UI_IMAGE


class FargateStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        cluster: ecs.Cluster,
        dns_namespace: ecs.CloudMapNamespace,
        core_database: rds.DatabaseInstance,
        core_security_group: ec2.SecurityGroup,
        temporal_database: rds.DatabaseInstance,
        temporal_security_group: ec2.SecurityGroup,
        listener: elbv2.ApplicationListener,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        ### Gather secrets
        tracecat_secrets = {
            "TRAECCAT__DB_ENCRYPTION_KEY": ecs.Secret.from_secrets_manager(
                secretsmanager.Secret.from_secret_partial_arn(
                    self,
                    "TracecatDbEncryptionKey",
                    secret_partial_arn=secretsmanager.Secret.from_secret_name_v2(
                        self,
                        "TracecatPartialDbEncryptionKey",
                        secret_arn=os.environ["DB_ENCRYPTION_KEY_NAME"],
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
                        secret_arn=os.environ["SERVICE_KEY_NAME"],
                    ).secret_arn,
                )
            ),
            "TRACECAT__SIGNING_KEY": ecs.Secret.from_secrets_manager(
                secretsmanager.Secret.from_secret_partial_arn(
                    self,
                    "TracecatSigningKey",
                    secret_partial_arn=secretsmanager.Secret.from_secret_name_v2(
                        self,
                        "TracecatPartialSigningKey",
                        secret_arn=os.environ["SIGNING_KEY_NAME"],
                    ).secret_arn,
                )
            ),
        }

        tracecat_ui_secrets = {
            "CLERK_FRONTEND_API_URL": ecs.Secret.from_secrets_manager(
                secretsmanager.Secret.from_secret_partial_arn(
                    self,
                    "ClerkFrontendApiUrl",
                    secret_partial_arn=secretsmanager.Secret.from_secret_name_v2(
                        self,
                        "ClerkPartialFrontendApiUrl",
                        secret_arn=os.environ["CLERK_FRONTEND_API_URL"],
                    ).secret_arn,
                )
            ),
            "CLERK_SECRET_KEY": ecs.Secret.from_secrets_manager(
                secretsmanager.Secret.from_secret_partial_arn(
                    self,
                    "ClerkSecretKey",
                    secret_partial_arn=secretsmanager.Secret.from_secret_name_v2(
                        self,
                        "ClerkPartialSecretKey",
                        secret_arn=os.environ["CLERK_SECRET_KEY"],
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

        ### Shared environment
        tracecat_environment = {
            "LOG_LEVEL": os.environ["LOG_LEVEL"],
            "TRACECAT__API_URL": os.environ["TRACECAT__API_URL"],
            "TRACECAT__APP_ENV": os.environ["TRACECAT__APP_ENV"],
            "TRACECAT__DB_URI": core_database.db_instance_endpoint_address,
            "TRACECAT__DISABLE_AUTH": os.environ["TRACECAT__DISABLE_AUTH"],
            "TRACECAT__PUBLIC_RUNNER_URL": os.environ["TRACECAT__PUBLIC_RUNNER_URL"],
        }

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

        ### API Service
        api_task_definition = ecs.FargateTaskDefinition(
            self,
            "ApiTaskDefinition",
            execution_role=execution_role,
            task_role=task_role,
        )
        api_task_definition.add_container(  # noqa
            "ApiContainer",
            image=TRACECAT_IMAGE,
            environment=tracecat_environment,
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
        ecs.FargateService(
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

        ### Worker Service
        worker_task_definition = ecs.FargateTaskDefinition(
            self,
            "WorkerTaskDefinition",
            execution_role=execution_role,
            task_role=task_role,
        )
        worker_task_definition.add_container(  # noqa
            "WorkerContainer",
            image=TRACECAT_IMAGE,
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
        ui_task_definition = ecs.FargateTaskDefinition(
            self,
            "TracecatUiTaskDefinition",
            execution_role=execution_role,
            task_role=task_role,
        )
        ui_task_definition.add_container(  # noqa
            "TracecatUiContainer",
            image=TRACECAT_UI_IMAGE,
            environment=tracecat_environment,
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
        ui_ecs_service = ecs.FargateService(
            self,
            "TracecatUiFargateService",
            cluster=cluster,
            service_name="tracecat-ui",
            # Attach the security group to your ECS service
            task_definition=ui_task_definition,
            security_groups=[core_security_group],
            capacity_provider_strategies=[capacity_provider_strategy],
        )

        ### Temporal Service
        temporal_task_definition = ecs.FargateTaskDefinition(
            self,
            "TemporalTaskDefinition",
            execution_role=execution_role,
            task_role=task_role,
        )
        temporal_task_definition.add_container(
            "TemporalContainer",
            image=TEMPORAL_IMAGE,
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
            execution_role=execution_role,
            task_role=task_role,
        )
        temporal_ui_task_definition.add_container(
            "TemporalUiContainer",
            image=TEMPORAL_UI_IMAGE,
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

        ### (Optional) Enable external access to the UI
        app_domain_name = os.getenv("APP_DOMAIN_NAME")
        if app_domain_name is not None:
            ui_target_group = elbv2.ApplicationTargetGroup(
                self,
                "TracecatUiTargetGroup",
                target_type=elbv2.TargetType.IP,
                port=8000,
                protocol=elbv2.ApplicationProtocol.HTTP,
                vpc=cluster.vpc,
                health_check=elbv2.HealthCheck(
                    path="/health",
                    interval=Duration.seconds(120),
                    timeout=Duration.seconds(10),
                    healthy_threshold_count=5,
                    unhealthy_threshold_count=2,
                ),
            )
            ui_target_group.add_target(
                ui_ecs_service.load_balancer_target(
                    container_name="TracecatUiContainer", container_port=3000
                )
            )
            listener.add_action(
                "TracecatUiTarget",
                priority=10,
                conditions=[elbv2.ListenerCondition.host_headers([app_domain_name])],
                action=elbv2.ListenerAction.forward(target_groups=[ui_target_group]),
            )