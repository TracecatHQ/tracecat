#!/bin/bash

kubectl get secret -n $KEYS_SECRET_NAMESPACE $KEYS_SECRET_NAME
# TODO also read the returned secret and check contents?
# or maybe check whether the output contains "NotFound" ?

if [[ 0 != $? ]]; then
  serviceKey=`openssl rand -hex 32`;
  signingSecret=`openssl rand -hex 32`;
  dbEncryptionKey=`cat /init-dir/db-encryption-key`;
  # TODO mark immutable!!
  kubectl create secret generic -n $KEYS_SECRET_NAMESPACE $KEYS_SECRET_NAME \
    --from-literal=SERVICE_KEY=$serviceKey \
    --from-literal=SIGNING_SECRET=$signingSecret \
    --from-literal=DB_ENCRYPTION_KEY=$dbEncryptionKey
fi
