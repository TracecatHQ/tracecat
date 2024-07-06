from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk import core
from constructs import Construct

# EC2 instance type for databases
INSTANCE_TYPE = ec2.InstanceType.of(
    ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.MEDIUM
)
# 100 GB storage
ALLOCATED_STORAGE = 100
# Use General Purpose SSD (GP2) for balanced performance
STORAGE_TYPE = rds.StorageType.GP2
# Adjusted IOPS based on GP2
IOPS = 1000
# Retain backups for 7 days
BACKUP_RETENTION = core.Duration.days(7)
# Monitoring interval of 60 seconds
MONITORING_INTERVAL = core.Duration.seconds(60)
# Preferred backup window
PREFERRED_BACKUP_WINDOW = "03:00-04:00"
# Preferred maintenance window
PREFERRED_MAINTENANCE_WINDOW = "Sun:04:00-Sun:05:00"
# Enable performance insights
PERFORMANCE_INSIGHTS = True
PERFORMANCE_INSIGHT_RETENTION = rds.PerformanceInsightRetention.LONG_TERM


class RdsStack(core.Stack):
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
            engine_version: rds.IInstanceEngine,
            security_group: ec2.SecurityGroup,
        ) -> None:
            db = rds.DatabaseInstance(
                self,
                instance_name,
                engine=engine_version,
                instance_type=INSTANCE_TYPE,
                vpc=vpc,
                multi_az=True,
                allocated_storage=ALLOCATED_STORAGE,
                storage_type=STORAGE_TYPE,
                iops=IOPS,
                credentials=rds.Credentials.from_generated_secret(db_name),
                deletion_protection=True,
                database_name=db_name,
                backup_retention=BACKUP_RETENTION,
                monitoring_interval=MONITORING_INTERVAL,
                removal_policy=core.RemovalPolicy.RETAIN,
                security_groups=[security_group],
                storage_encrypted=True,
                preferred_backup_window=PREFERRED_BACKUP_WINDOW,
                preferred_maintenance_window=PREFERRED_MAINTENANCE_WINDOW,
                enable_performance_insights=PERFORMANCE_INSIGHTS,
                performance_insight_retention=PERFORMANCE_INSIGHT_RETENTION,
            )
            return db

        # Create PostgreSQL instances
        tracecat_database = create_rds_instance(
            "TracecatRDSInstance",
            "tracecat-postgres",
            rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16
            ),
            core_security_group,
        )
        self.tracecat_database = tracecat_database

        temporal_database = create_rds_instance(
            "TemporalRDSInstance",
            "temporal-postgres",
            rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_13
            ),
            temporal_security_group,
        )
        self.temporal_database = temporal_database
