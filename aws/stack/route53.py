from aws_cdk import Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_route53 as route53
from constructs import Construct

from .config import APP_DOMAIN_NAME, CERTIFICATE_ARN


class Route53Stack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        self.hosted_zone = route53.HostedZone.from_lookup(
            self,
            "HostedZone",
            domain_name=APP_DOMAIN_NAME,
        )
        self.certificate = acm.Certificate.from_certificate_arn(
            self,
            "Certificate",
            certificate_arn=CERTIFICATE_ARN,
        )
