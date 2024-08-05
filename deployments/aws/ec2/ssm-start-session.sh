#!/bin/bash

INSTANCE_ID=$(terraform output -raw instance_id)
LOCAL_PORT=80
REMOTE_PORT=80

aws ssm start-session \
    --target $INSTANCE_ID \
    --document-name AWS-StartPortForwardingSession \
    --parameters "{\"portNumber\":[\"$REMOTE_PORT\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}"
