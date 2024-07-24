import os

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from .config import IS_PRODUCTION

# RDS settings
INSTANCE_TYPE = ec2.InstanceType.of(
    ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.MEDIUM
)
STORAGE_TYPE = rds.StorageType.GP2
BACKUP_RETENTION = Duration.days(7)
MONITORING_INTERVAL = Duration.seconds(60)
PREFERRED_BACKUP_WINDOW = "03:00-04:00"
PREFERRED_MAINTENANCE_WINDOW = "Sun:04:00-Sun:05:00"
STORAGE_TYPE = rds.StorageType.GP2


class RdsStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc: ec2.Vpc,
        **kwargs,
    ):
        super().__init__(scope, id, **kwargs)

        # Create security group for API to RDS communication
        core_db_security_group = ec2.SecurityGroup(
            self,
            "CoreDbSecurityGroup",
            vpc=vpc,
            description="Security group for Tracecat API to RDS communication",
        )
        core_db_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(5432),
            description="Allow inbound traffic to PostgreSQL database on port 5432",
        )

        # CReate security group for Temporal to RDS communication
        temporal_db_security_group = ec2.SecurityGroup(
            self,
            "TemporalDbSecurityGroup",
            vpc=vpc,
            description="Security group for Temporal to RDS communication",
        )
        temporal_db_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(5432),
            description="Allow inbound traffic to PostgreSQL database on port 5432",
        )

        def create_rds_instance(
            instance_name: str,
            db_name: str,
            password_secret_name: str,
            allocated_storage: int,
            engine_version: rds.IInstanceEngine,
            security_group: ec2.SecurityGroup,
        ) -> rds.DatabaseInstance:
            db_secret = secretsmanager.Secret.from_secret_partial_arn(
                self,
                f"{instance_name}Secret",
                secret_partial_arn=secretsmanager.Secret.from_secret_name_v2(
                    self,
                    f"{instance_name}PartialSecret",
                    secret_name=password_secret_name,
                ).secret_arn,
            )
            db = rds.DatabaseInstance(
                self,
                instance_name,
                engine=engine_version,
                instance_type=INSTANCE_TYPE,
                vpc=vpc,
                multi_az=IS_PRODUCTION,
                allocated_storage=allocated_storage,
                storage_type=STORAGE_TYPE,
                credentials=rds.Credentials.from_password(
                    username="postgres", password=db_secret.secret_value
                ),
                deletion_protection=IS_PRODUCTION,
                database_name=db_name,
                backup_retention=BACKUP_RETENTION,
                monitoring_interval=MONITORING_INTERVAL,
                removal_policy=RemovalPolicy.RETAIN
                if IS_PRODUCTION
                else RemovalPolicy.DESTROY,
                security_groups=[security_group],
                storage_encrypted=True,
                preferred_backup_window=PREFERRED_BACKUP_WINDOW,
                preferred_maintenance_window=PREFERRED_MAINTENANCE_WINDOW,
            )
            return db, db_secret

        # Create PostgreSQL instances
        tracecat_database, tracecat_db_secret = create_rds_instance(
            "TracecatRDSInstance",
            db_name="tracecat",
            password_secret_name=os.environ["TRACECAT_DB_PASS_NAME"],
            allocated_storage=10,
            engine_version=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16
            ),
            security_group=core_db_security_group,
        )
        self.core_database = tracecat_database
        self.core_db_secret = tracecat_db_secret

        temporal_database, temporal_db_secret = create_rds_instance(
            "TemporalRDSInstance",
            db_name="temporal",
            password_secret_name=os.environ["TEMPORAL_DB_PASS_NAME"],
            allocated_storage=5,
            engine_version=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_13
            ),
            security_group=temporal_db_security_group,
        )
        self.temporal_database = temporal_database
        self.temporal_db_secret = temporal_db_secret

        self.core_db_security_group = core_db_security_group
        self.temporal_db_security_group = temporal_db_security_group
