"""AWS Inspector integration.

Inspector is a vulnerability management service that continuously scans for security vulnerabilities in the following AWS workloads:

    - EC2 instances
    - Container images in ECR
    - AWS Lambda functions

Authentication method: Cross-account AWS Role

IAM Policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "inspector:ListFindings",
        "inspector.DescribeFindings"
      ],
      "Resource": "*"
    }
  ]
}
```


Trust Relationship:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::YOUR_SAAS_PROVIDER_ACCOUNT_ID:root"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

Supported APIs:

```python
list_findings = {
    "endpoint": ["boto3.Inspector.list_findings", "boto3.GuardDuty.describe_findings"],
    "user_agent": "boto3",
    "ocsf_schema": "array[vulnerability_finding]",
    "reference": "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/inspector.html"
}
```
"""
