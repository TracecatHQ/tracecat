#!/bin/bash
# Requires SSM Session Manager plugin to be installed on the local machine:
# https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html

# Ask the user for the INSTANCE_ID
read -p "Please enter the EC2 'instance_id' (you can find this in Terraform outputs): " INSTANCE_ID

# Check if INSTANCE_ID is empty
if [[ -z "$INSTANCE_ID" ]]; then
    echo "Error: No INSTANCE_ID provided. Exiting."
    exit 1
fi
LOCAL_PORT=8080
REMOTE_PORT=80

aws ssm start-session \
    --target $INSTANCE_ID \
    --document-name AWS-StartPortForwardingSession \
    --parameters "{\"portNumber\":[\"$REMOTE_PORT\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}"
