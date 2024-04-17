import os

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_efs as efs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk.aws_certificatemanager import Certificate
from aws_cdk.aws_route53_targets import LoadBalancerTarget
from constructs import Construct

TRACECAT__APP_ENV = os.environ.get("TRACECAT__APP_ENV", "staging")
AWS_ECR__API_IMAGE_URI = os.environ["AWS_ECR__API_IMAGE_URI"]
AWS_ECR__SCHEDULER_IMAGE_URI = os.environ["AWS_ECR__SCHEDULER_IMAGE_URI"]
AWS_SECRET__ARN = os.environ["AWS_SECRET__ARN"]
AWS_ROUTE53__HOSTED_ZONE_ID = os.environ["AWS_ROUTE53__HOSTED_ZONE_ID"]
AWS_ROUTE53__HOSTED_ZONE_NAME = os.environ["AWS_ROUTE53__HOSTED_ZONE_NAME"]
PREFIXED_AWS_ROUTE53__HOSTED_ZONE_NAME = (
    f"staging.{AWS_ROUTE53__HOSTED_ZONE_NAME}"
    if TRACECAT__APP_ENV == "staging"
    else AWS_ROUTE53__HOSTED_ZONE_NAME
)
AWS_ACM__CERTIFICATE_ARN = os.environ["AWS_ACM__CERTIFICATE_ARN"]
AWS_ACM__API_CERTIFICATE_ARN = os.environ["AWS_ACM__API_CERTIFICATE_ARN"]
AWS_ACM__RUNNER_CERTIFICATE_ARN = os.environ["AWS_ACM__RUNNER_CERTIFICATE_ARN"]

if TRACECAT__APP_ENV == "production":
    CPU = 512
    MEMORY_LIMIT_MIB = 1024
else:
    CPU = 256
    MEMORY_LIMIT_MIB = 512


class TracecatEngineStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # Create cluster
        vpn_name = f"tracecat-vpc-{TRACECAT__APP_ENV}"
        cluster_name = f"tracecat-ecs-cluster-{TRACECAT__APP_ENV}"
        vpc = ec2.Vpc(self, "Vpc", vpc_name=vpn_name)
        cluster = ecs.Cluster(self, "Cluster", cluster_name=cluster_name, vpc=vpc)
        cluster.add_default_cloud_map_namespace(
            name="tracecat.local", vpc=vpc, use_for_service_connect=True
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
        api_cert = Certificate.from_certificate_arn(
            self, "ApiCertificate", certificate_arn=AWS_ACM__API_CERTIFICATE_ARN
        )
        runner_cert = Certificate.from_certificate_arn(
            self, "RunnerCertificate", certificate_arn=AWS_ACM__RUNNER_CERTIFICATE_ARN
        )

        ### Environment variables
        if TRACECAT__APP_ENV in ("production", "staging"):
            shared_env = {
                "TRACECAT__APP_ENV": TRACECAT__APP_ENV,
                # Use http and internal DNS for internal communication
                "TRACECAT__API_URL": "http://api.tracecat.local",
                "TRACECAT__RUNNER_URL": "http://runner.tracecat.local",
            }
        else:
            shared_env = {"TRACECAT__APP_ENV": TRACECAT__APP_ENV}

        # API env vars
        api_env = {
            "API_MODULE": "tracecat.api.app:app",
            "SUPABASE_JWT_ALGORITHM": "HS256",
            **shared_env,
        }

        # Runner env vars
        runner_env = {
            "API_MODULE": "tracecat.runner.app:app",
            "PORT": "8001",
            **shared_env,
        }

        ### Secrets
        tracecat_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "Secret", secret_complete_arn=AWS_SECRET__ARN
        )
        shared_secrets = {
            "RABBITMQ_URI": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="rabbitmq-uri"
            ),
            "TRACECAT__DB_ENCRYPTION_KEY": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="db-encryption-key"
            ),
            "TRACECAT__DB_URI": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="db-uri"
            ),
            "TRACECAT__SERVICE_KEY": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="service-key"
            ),
            "TRACECAT__SIGNING_SECRET": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="signing-secret"
            ),
        }
        api_secrets = {
            **shared_secrets,
            "SUPABASE_JWT_SECRET": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="supabase-jwt-secret"
            ),
            "OPENAI_API_KEY": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="openai-api-key"
            ),
        }
        runner_secrets = {
            **shared_secrets,
            "OPENAI_API_KEY": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="openai-api-key"
            ),
            "RESEND_API_KEY": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="resend-api-key"
            ),
        }
        rabbitmq_secrets = {
            "RABBITMQ_DEFAULT_USER": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="rabbitmq-default-user"
            ),
            "RABBITMQ_DEFAULT_PASS": ecs.Secret.from_secrets_manager(
                tracecat_secret, field="rabbitmq-default-pass"
            ),
        }

        ### Security Groups
        # 1. Create ALB security group
        alb_security_group = ec2.SecurityGroup(
            self,
            "AlbSecurityGroup",
            vpc=vpc,
            description="Security group for ALB",
        )
        alb_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(443),
            description="Allow HTTPS traffic from the Internet",
        )
        alb_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(80),
            description="Allow HTTP traffic for redirection to HTTPS",
        )

        # 2. API and runner security groups
        api_security_group = ec2.SecurityGroup(
            self,
            "ApiSecurityGroup",
            vpc=vpc,
            description="Security group for API service",
        )
        runner_security_group = ec2.SecurityGroup(
            self,
            "RunnerSecurityGroup",
            vpc=vpc,
            description="Security group for Runner service",
        )

        # 3. API and runner ingress / egress rules
        api_security_group.add_ingress_rule(
            peer=alb_security_group,
            connection=ec2.Port.tcp(80),
            description="Allow HTTP traffic from the ALB",
        )
        runner_security_group.add_ingress_rule(
            peer=alb_security_group,
            connection=ec2.Port.tcp(80),
            description="Allow HTTP traffic from the ALB",
        )
        # Ingress rules for API and Runner
        api_security_group.add_ingress_rule(
            peer=runner_security_group,
            connection=ec2.Port.tcp(80),
            description="Allow traffic from Runner to API",
        )
        runner_security_group.add_ingress_rule(
            peer=api_security_group,
            connection=ec2.Port.tcp(80),
            description="Allow traffic from API to Runner",
        )

        # 4. Scheduler security group
        scheduler_security_group = ec2.SecurityGroup(
            self,
            "SchedulerSecurityGroup",
            vpc=vpc,
            description="Security group for Scheduler service",
        )
        # Allow Scheduler to receive traffic from API and Runner
        scheduler_security_group.add_ingress_rule(
            peer=api_security_group,
            connection=ec2.Port.tcp(80),
            description="Allow traffic from API to Scheduler",
        )
        scheduler_security_group.add_ingress_rule(
            peer=runner_security_group,
            connection=ec2.Port.tcp(80),
            description="Allow traffic from Runner to Scheduler",
        )
        # Allow Scheduler to send traffic to API and Runner
        scheduler_security_group.add_egress_rule(
            peer=api_security_group,
            connection=ec2.Port.tcp(80),
            description="Allow Scheduler to connect to API",
        )
        scheduler_security_group.add_egress_rule(
            peer=runner_security_group,
            connection=ec2.Port.tcp(80),
            description="Allow Scheduler to connect to Runner",
        )

        # 5. RabbitMQ security group
        rabbitmq_security_group = ec2.SecurityGroup(
            self,
            "RabbitmqSecurityGroup",
            vpc=vpc,
            description="Security group for RabbitMQ service",
        )
        api_security_group.add_egress_rule(
            peer=rabbitmq_security_group,
            connection=ec2.Port.tcp(5672),
            description="Allow API to connect to RabbitMQ on AMQP port",
        )
        runner_security_group.add_egress_rule(
            peer=rabbitmq_security_group,
            connection=ec2.Port.tcp(5672),
            description="Allow Runner to connect to RabbitMQ on AMQP port",
        )
        rabbitmq_security_group.add_ingress_rule(
            peer=api_security_group,
            connection=ec2.Port.tcp(5672),
            description="Allow incoming AMQP traffic from API service",
        )
        rabbitmq_security_group.add_ingress_rule(
            peer=runner_security_group,
            connection=ec2.Port.tcp(5672),
            description="Allow incoming AMQP traffic from Runner service",
        )

        # 5. EFS security group
        shared_efs_security_group = ec2.SecurityGroup(
            self,
            "SharedEfsSecurityGroup",
            vpc=vpc,
            description="Security group for EFS accessible by API and Runner",
        )
        # Allow NFS traffic from API and Runner to the EFS
        shared_efs_security_group.add_ingress_rule(
            peer=api_security_group,
            connection=ec2.Port.tcp(2049),
            description="Allow NFS traffic from API service",
        )
        shared_efs_security_group.add_ingress_rule(
            peer=runner_security_group,
            connection=ec2.Port.tcp(2049),
            description="Allow NFS traffic from Runner service",
        )

        # 6. RabbitMQ EFS Security Group
        rabbitmq_efs_security_group = ec2.SecurityGroup(
            self,
            "RabbitmqEfsSecurityGroup",
            vpc=vpc,
            description="Security group EFS accessible by RabbitMQ only",
        )
        # Restrict NFS traffic to just the RabbitMQ service
        rabbitmq_efs_security_group.add_ingress_rule(
            peer=rabbitmq_security_group,
            connection=ec2.Port.tcp(2049),
            description="Allow NFS traffic from RabbitMQ service",
        )

        # Create shared EFS for API and Runner
        shared_file_system = efs.FileSystem(
            self,
            "SharedFileSystem",
            vpc=vpc,
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            throughput_mode=efs.ThroughputMode.BURSTING,
            security_group=shared_efs_security_group,
        )
        # Define EFS access point for apiuser
        efs_access_point = shared_file_system.add_access_point(
            "AccessPoint",
            path="/apiuser",
            create_acl=efs.Acl(owner_uid="1001", owner_gid="1001", permissions="0755"),
            posix_user=efs.PosixUser(uid="1001", gid="1001"),
        )
        # Create Volume
        volume_name = f"TracecatVolume-{TRACECAT__APP_ENV}"
        shared_volume = ecs.Volume(
            name=volume_name,
            efs_volume_configuration=ecs.EfsVolumeConfiguration(
                file_system_id=shared_file_system.file_system_id,
                transit_encryption="ENABLED",
                authorization_config=ecs.AuthorizationConfig(
                    access_point_id=efs_access_point.access_point_id
                ),
            ),
        )

        # Task execution IAM role (used across API and runner)
        logs_group_prefix = f"arn:aws:logs:{self.region}:{self.account}:log-group:"
        if TRACECAT__APP_ENV == "production":
            logs_group_pattern = f"{logs_group_prefix}/ecs/tracecat-*:*"
        else:
            logs_group_pattern = (
                f"{logs_group_prefix}/ecs/tracecat-{TRACECAT__APP_ENV}*:*"
            )

        execution_role = iam.Role(
            self,
            "ExecutionRole",
            role_name=f"TracecatFargateServiceExecutionRole-{TRACECAT__APP_ENV}",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        iam.Policy(
            self,
            "ExecutionRolePolicy",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                    resources=[logs_group_pattern],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage",
                        "ecr:GetAuthorizationToken",
                    ],
                    resources=[
                        f"arn:aws:ecr:{self.region}:{self.account}:repository/tracecat",
                        f"arn:aws:ecr:{self.region}:{self.account}:repository/tracecat-scheduler",
                    ],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["ecr:GetAuthorizationToken"],
                    # Note: ecr:GetAuthorizationToken requires access on the service level, not specific repositories
                    resources=["*"],
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

        # Task role
        task_role = iam.Role(
            self,
            "TaskRole",
            role_name=f"TracecatTaskRole-{TRACECAT__APP_ENV}",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        iam.Policy(
            self,
            "TaskRolePolicy",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "elasticfilesystem:ClientMount",
                        "elasticfilesystem:ClientWrite",
                        "elasticfilesystem:DescribeFileSystems",
                        "elasticfilesystem:DescribeMountTargets",
                        "elasticfilesystem:DescribeMountTargetSecurityGroups",
                    ],
                    resources=[
                        f"arn:aws:elasticfilesystem:{self.region}:{self.account}:file-system/{shared_file_system.file_system_id}"
                    ],
                ),
            ],
            roles=[task_role],
        )

        # Set up a log group
        log_group_name = "/ecs/tracecat"
        if TRACECAT__APP_ENV != "production":
            log_group_name = f"{log_group_name}-{TRACECAT__APP_ENV}"
        log_group = logs.LogGroup(
            self,
            "TracecatLogGroup",
            log_group_name=log_group_name,
            removal_policy=RemovalPolicy.DESTROY,
        )

        ### Tracecat API Fargate Service
        # Task definition
        api_task_definition = ecs.FargateTaskDefinition(
            self,
            "ApiTaskDefinition",
            execution_role=execution_role,
            task_role=task_role,
            cpu=CPU,
            memory_limit_mib=MEMORY_LIMIT_MIB,
        )
        # Volume
        api_task_definition.add_volume(
            name=volume_name,
            efs_volume_configuration=shared_volume.efs_volume_configuration,
        )
        # Container
        api_container = api_task_definition.add_container(  # noqa
            "ApiContainer",
            image=ecs.ContainerImage.from_registry(AWS_ECR__API_IMAGE_URI),
            cpu=CPU,
            memory_limit_mib=MEMORY_LIMIT_MIB,
            environment=api_env,
            secrets=api_secrets,
            port_mappings=[
                ecs.PortMapping(
                    container_port=8000,
                    host_port=80,
                    name="api",
                    app_protocol=ecs.Protocol.HTTP,
                )
            ],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="tracecat-api", log_group=log_group
            ),
        )
        api_container.add_mount_points(
            ecs.MountPoint(
                container_path="/home/apiuser/.tracecat",
                read_only=False,
                source_volume=volume_name,
            )
        )
        # ECS service
        api_ecs_service = ecs.FargateService(
            self,
            "TracecatApiFargateService",
            cluster=cluster,
            service_name="tracecat-api",
            # Attach the security group to your ECS service
            task_definition=api_task_definition,
            security_groups=[api_security_group],
            service_connect_configuration=ecs.ServiceConnectProps(
                services=[ecs.ServiceConnectService(port_mapping_name="api")]
            ),
        )
        # API target group
        api_target_group = elbv2.ApplicationTargetGroup(
            self,
            "TracecatApiTargetGroup",
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
        api_target_group.add_target(
            api_ecs_service.load_balancer_target(
                container_name="ApiContainer", container_port=8000
            )
        )

        ### Tracecat Runner Fargate Service#
        # Task definition
        runner_task_definition = ecs.FargateTaskDefinition(
            self,
            "RunnerTaskDefinition",
            execution_role=execution_role,
            task_role=task_role,
            cpu=CPU,
            memory_limit_mib=MEMORY_LIMIT_MIB,
        )
        # Volume
        runner_task_definition.add_volume(
            name=volume_name,
            efs_volume_configuration=shared_volume.efs_volume_configuration,
        )
        # Container
        runner_container = runner_task_definition.add_container(  # noqa
            "RunnerContainer",
            image=ecs.ContainerImage.from_registry(AWS_ECR__API_IMAGE_URI),
            cpu=CPU,
            memory_limit_mib=MEMORY_LIMIT_MIB,
            environment=runner_env,
            secrets=runner_secrets,
            port_mappings=[
                ecs.PortMapping(
                    container_port=8001,
                    host_port=80,
                    name="runner",
                    app_protocol=ecs.Protocol.HTTP,
                )
            ],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="tracecat-runner", log_group=log_group
            ),
        )
        runner_container.add_mount_points(
            ecs.MountPoint(
                container_path="/home/apiuser/.tracecat",
                read_only=False,
                source_volume=volume_name,
            )
        )
        # ECS service
        runner_ecs_service = ecs.FargateService(
            self,
            "TracecatRunnerFargateService",
            cluster=cluster,
            service_name="tracecat-runner",
            task_definition=runner_task_definition,
            # Attach the security group to your ECS service
            security_groups=[runner_security_group],
            service_connect_configuration=ecs.ServiceConnectProps(
                services=[ecs.ServiceConnectService(port_mapping_name="runner")]
            ),
        )
        # Runner target group
        runner_target_group = elbv2.ApplicationTargetGroup(
            self,
            "TracecatRunnerTargetGroup",
            target_type=elbv2.TargetType.IP,
            port=8001,
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
        runner_target_group.add_target(
            runner_ecs_service.load_balancer_target(
                container_name="RunnerContainer", container_port=8001
            )
        )

        ### Scheduler Fargate Service
        # Task definition
        scheduler_task_definition = ecs.FargateTaskDefinition(
            self,
            "SchedulerTaskDefinition",
            execution_role=execution_role,
            task_role=task_role,
            cpu=CPU,
            memory_limit_mib=MEMORY_LIMIT_MIB,
        )
        # Container
        scheduler_task_definition.add_container(  # noqa
            "SchedulerContainer",
            image=ecs.ContainerImage.from_registry(AWS_ECR__SCHEDULER_IMAGE_URI),
            cpu=CPU,
            memory_limit_mib=MEMORY_LIMIT_MIB,
            environment=shared_env,
            secrets=shared_secrets,
            port_mappings=[
                ecs.PortMapping(
                    container_port=8002,
                    host_port=80,
                    name="scheduler",
                    app_protocol=ecs.Protocol.HTTP,
                )
            ],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="tracecat-scheduler", log_group=log_group
            ),
        )
        # ECS service
        ecs.FargateService(
            self,
            "TracecatSchedulerFargateService",
            cluster=cluster,
            service_name="tracecat-scheduler",
            task_definition=scheduler_task_definition,
            security_groups=[scheduler_security_group],
            service_connect_configuration=ecs.ServiceConnectProps(
                services=[ecs.ServiceConnectService(port_mapping_name="scheduler")]
            ),
        )

        ### RabbitMQ Fargate Service
        # Create the execution role for RabbitMQ
        rabbitmq_execution_role = iam.Role(
            self,
            "RabbitMqExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        # Define the policy for logging to CloudWatch Logs
        rabbitmq_logging_policy = iam.PolicyStatement(
            actions=["logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[
                f"arn:aws:logs:{self.region}:{self.account}:log-group:/ecs/rabbitmq:*"
            ],
        )
        rabbitmq_execution_role.add_to_policy(rabbitmq_logging_policy)
        # Create task role for RabbitMQ
        rabbitmq_task_role = iam.Role(
            self,
            "RabbitMqTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # Isolated EFS for RabbitMQ
        rabbitmq_file_system = efs.FileSystem(
            self,
            "RabbitMqEfs",
            vpc=vpc,
            lifecycle_policy=efs.LifecyclePolicy.AFTER_14_DAYS,
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            throughput_mode=efs.ThroughputMode.BURSTING,
            security_group=rabbitmq_efs_security_group,
        )
        rabbitmq_efs_access_point = rabbitmq_file_system.add_access_point(
            "RabbitMqAccessPoint",
            path="/data",
            posix_user=efs.PosixUser(
                uid="999", gid="999"
            ),  # UID and GID that RabbitMQ uses
            create_acl=efs.Acl(owner_uid="999", owner_gid="999", permissions="755"),
        )

        # Create Volume for RabbitMQ
        rabbitmq_volume_name = f"RabbitmqVolume-{TRACECAT__APP_ENV}"
        rabbitmq_volume = ecs.Volume(
            name=rabbitmq_volume_name,
            efs_volume_configuration=ecs.EfsVolumeConfiguration(
                file_system_id=rabbitmq_file_system.file_system_id,
                transit_encryption="ENABLED",
                authorization_config=ecs.AuthorizationConfig(
                    access_point_id=rabbitmq_efs_access_point.access_point_id
                ),
            ),
        )

        # RabbitMQ Fargate service
        rabbitmq_task_definition = ecs.FargateTaskDefinition(
            self,
            "RabbitMqTaskDefinition",
            execution_role=rabbitmq_execution_role,
            task_role=rabbitmq_task_role,
            cpu=256,
            memory_limit_mib=512,
        )
        rabbitmq_task_definition.add_volume(
            name=rabbitmq_volume_name,
            efs_volume_configuration=rabbitmq_volume.efs_volume_configuration,
        )
        rabbitmq_container = rabbitmq_task_definition.add_container(
            "RabbitMqContainer",
            image=ecs.ContainerImage.from_registry("rabbitmq:3.13-management"),
            user="999:999",  # UID and GID that RabbitMQ uses
            cpu=256,
            memory_limit_mib=512,
            secrets=rabbitmq_secrets,
            port_mappings=[ecs.PortMapping(container_port=5672, name="rabbitmq")],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="rabbitmq", log_group=log_group
            ),
        )
        rabbitmq_container.add_mount_points(
            ecs.MountPoint(
                container_path="/var/lib/rabbitmq/mnesia",
                read_only=False,
                source_volume=rabbitmq_volume_name,
            )
        )
        ecs.FargateService(
            self,
            "RabbitMqFargateService",
            cluster=cluster,
            service_name="rabbitmq",
            task_definition=rabbitmq_task_definition,
            security_groups=[rabbitmq_security_group],
            service_connect_configuration=ecs.ServiceConnectProps(
                services=[ecs.ServiceConnectService(port_mapping_name="rabbitmq")]
            ),
        )

        # Load balancer
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "TracecatEngineAlb",
            vpc=cluster.vpc,
            internet_facing=True,
            load_balancer_name=f"tracecat-engine-alb-{TRACECAT__APP_ENV}",
        )
        alb.add_listener(
            # Redirect HTTP to HTTPS
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

        # Main HTTPS listener
        listener = alb.add_listener(
            "DefaultHttpsListener",
            port=443,
            certificates=[cert, api_cert, runner_cert],
            default_action=elbv2.ListenerAction.fixed_response(404),
        )
        listener.add_action(
            "RootRedirect",
            priority=30,
            conditions=[elbv2.ListenerCondition.path_patterns(["/"])],
            action=elbv2.ListenerAction.redirect(
                host=f"api.{PREFIXED_AWS_ROUTE53__HOSTED_ZONE_NAME}",  # Redirect to the API subdomain
                protocol="HTTPS",
                port="443",
                path="/",
                permanent=True,  # Permanent redirect
            ),
        )

        # Add subdomain listeners
        listener.add_action(
            "ApiTarget",
            priority=10,
            conditions=[
                elbv2.ListenerCondition.host_headers(
                    [f"api.{PREFIXED_AWS_ROUTE53__HOSTED_ZONE_NAME}"]
                )
            ],
            action=elbv2.ListenerAction.forward(target_groups=[api_target_group]),
        )
        listener.add_action(
            "RunnerTarget",
            priority=20,
            conditions=[
                elbv2.ListenerCondition.host_headers(
                    [f"runner.{PREFIXED_AWS_ROUTE53__HOSTED_ZONE_NAME}"]
                )
            ],
            action=elbv2.ListenerAction.forward(target_groups=[runner_target_group]),
        )

        # Create A record to point the hosted zone domain to the ALB
        route53.ARecord(
            self,
            "AliasRecord",
            record_name=PREFIXED_AWS_ROUTE53__HOSTED_ZONE_NAME,
            target=route53.RecordTarget.from_alias(LoadBalancerTarget(alb)),
            zone=hosted_zone,
        )
        # Create A record for api.domain.com pointing to the ALB
        route53.ARecord(
            self,
            "ApiAliasRecord",
            record_name=f"api.{PREFIXED_AWS_ROUTE53__HOSTED_ZONE_NAME}",
            target=route53.RecordTarget.from_alias(LoadBalancerTarget(alb)),
            zone=hosted_zone,
        )

        # Create A record for runner.domain.com pointing to the ALB
        route53.ARecord(
            self,
            "RunnerAliasRecord",
            record_name=f"runner.{PREFIXED_AWS_ROUTE53__HOSTED_ZONE_NAME}",
            target=route53.RecordTarget.from_alias(LoadBalancerTarget(alb)),
            zone=hosted_zone,
        )
