## Requirements and Setup

**Tested with:**

* Mac OS 13.4
* terraform 1.5.7

**Importante Not:** This terraform deployment builds an ECS Fargate Tracecat deployment with exposed public endpoints reachable through DNS.  It requires you to have a Route53 hosted zone.  It is mandatory that you have a hosted zone to build these resources.  Fill out the following terraform variables prior to building this:

1. In ```dns.tf```, please complete the following two variables to match your environment:
```
variable "hosted_zone_id" {
  description = "The ID of the hosted zone in Route53"
  default     = "Z03031931OSLRP2864ZXA"
}

variable "domain_name" {
  description = "The main domain name"
  default     = "tracecat.com"
}
```

**Credentials Setup:**

Generate an IAM programmatic access key that has permissions to build resources in your AWS account.  Setup your .env to load these environment variables.  You can also use the direnv tool to hook into your shell and populate the .envrc.  Should look something like this in your .env or .envrc:

```
export AWS_ACCESS_KEY_ID="VALUE"
export AWS_SECRET_ACCESS_KEY="VALUE"
```

## Build and Destroy Resources

### Run terraform init
```
terraform init
```

### Run terraform plan or apply
```
terraform apply -auto-approve
```
or
```
terraform plan -out=run.plan
terraform apply run.plan
```

### Destroy resources
```
terraform destroy -auto-approve
```

### View terraform created resources
To view the created endpoint resources:
```
terraform output
```
