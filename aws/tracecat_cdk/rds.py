from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from constructs import Construct

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
        core_security_group: ec2.SecurityGroup,
        temporal_security_group: ec2.SecurityGroup,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        def create_rds_instance(
            instance_name: str,
            db_name: str,
            allocated_storage: int,
            engine_version: rds.IInstanceEngine,
            security_group: ec2.SecurityGroup,
        ) -> rds.DatabaseInstance:
            db = rds.DatabaseInstance(
                self,
                instance_name,
                engine=engine_version,
                instance_type=INSTANCE_TYPE,
                vpc=vpc,
                multi_az=True,
                allocated_storage=allocated_storage,
                storage_type=STORAGE_TYPE,
                credentials=rds.Credentials.from_generated_secret(db_name),
                deletion_protection=True,
                database_name=db_name,
                backup_retention=BACKUP_RETENTION,
                monitoring_interval=MONITORING_INTERVAL,
                removal_policy=RemovalPolicy.RETAIN,
                security_groups=[security_group],
                storage_encrypted=True,
                preferred_backup_window=PREFERRED_BACKUP_WINDOW,
                preferred_maintenance_window=PREFERRED_MAINTENANCE_WINDOW,
            )
            return db

        # Create PostgreSQL instances
        tracecat_database = create_rds_instance(
            "TracecatRDSInstance",
            "tracecat-postgres",
            allocated_storage=10,
            engine_version=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16
            ),
            security_group=core_security_group,
        )
        self.core_database = tracecat_database

        temporal_database = create_rds_instance(
            "TemporalRDSInstance",
            "temporal-postgres",
            allocated_storage=5,
            engine_version=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_13
            ),
            security_group=temporal_security_group,
        )
        self.temporal_database = temporal_database
