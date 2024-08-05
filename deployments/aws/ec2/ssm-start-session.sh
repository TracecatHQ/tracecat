#!/bin/bash
# Requires SSM Session Manager plugin to be installed on the local machine:
# https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html

INSTANCE_ID=$(terraform output -raw instance_id)
LOCAL_PORT=8080
REMOTE_PORT=80

aws ssm start-session \
    --target $INSTANCE_ID \
    --document-name AWS-StartPortForwardingSession \
    --parameters "{\"portNumber\":[\"$REMOTE_PORT\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}"
