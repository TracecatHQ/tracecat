# Route53 DNS Record for ALB Ingress
# The ALB is created by the AWS Load Balancer Controller based on the Ingress resource.
# We need to create an A record alias pointing to the ALB after it's provisioned.

resource "null_resource" "dns_record" {
  triggers = {
    domain_name    = var.domain_name
    hosted_zone_id = var.hosted_zone_id
  }

  provisioner "local-exec" {
    command = <<-EOT
      # Wait for ingress to have an ADDRESS (ALB hostname)
      echo "Waiting for ALB to be provisioned..."
      for i in $(seq 1 60); do
        ALB_HOSTNAME=$(kubectl get ingress tracecat -n tracecat -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
        if [ -n "$ALB_HOSTNAME" ]; then
          echo "ALB hostname: $ALB_HOSTNAME"
          break
        fi
        echo "Waiting for ALB... ($i/60)"
        sleep 5
      done

      if [ -z "$ALB_HOSTNAME" ]; then
        echo "ERROR: ALB hostname not found after 5 minutes"
        exit 1
      fi

      # Get the ALB's canonical hosted zone ID
      ALB_HOSTED_ZONE=$(aws elbv2 describe-load-balancers --region ${local.aws_region} \
        --query "LoadBalancers[?DNSName=='$ALB_HOSTNAME'].CanonicalHostedZoneId" --output text)

      if [ -z "$ALB_HOSTED_ZONE" ]; then
        echo "ERROR: Could not determine ALB hosted zone ID"
        exit 1
      fi

      echo "Creating Route53 alias record..."
      aws route53 change-resource-record-sets \
        --hosted-zone-id ${var.hosted_zone_id} \
        --change-batch '{
          "Changes": [{
            "Action": "UPSERT",
            "ResourceRecordSet": {
              "Name": "${var.domain_name}",
              "Type": "A",
              "AliasTarget": {
                "HostedZoneId": "'$ALB_HOSTED_ZONE'",
                "DNSName": "'$ALB_HOSTNAME'",
                "EvaluateTargetHealth": true
              }
            }
          }]
        }'

      echo "DNS record created successfully"
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      aws route53 change-resource-record-sets \
        --hosted-zone-id ${self.triggers.hosted_zone_id} \
        --change-batch '{
          "Changes": [{
            "Action": "DELETE",
            "ResourceRecordSet": {
              "Name": "${self.triggers.domain_name}",
              "Type": "A",
              "AliasTarget": {
                "HostedZoneId": "Z18D5FSROUN65G",
                "DNSName": "placeholder.us-west-2.elb.amazonaws.com",
                "EvaluateTargetHealth": true
              }
            }
          }]
        }' 2>/dev/null || echo "DNS record already deleted or not found"
    EOT
  }

  depends_on = [helm_release.tracecat]
}
