#!/bin/bash

# Redirect all output to a log file
exec > >(tee /var/log/user-data.log) 2>&1

echo "Starting user data script execution"

# Install docker
yum update -y
amazon-linux-extras install docker -y
systemctl enable docker
systemctl start docker
usermod -a -G docker ec2-user
curl -L "https://github.com/docker/compose/releases/download/1.29.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Install tracecat
cd /home/ec2-user
curl -O https://raw.githubusercontent.com/TracecatHQ/tracecat/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/TracecatHQ/tracecat/main/env.sh
curl -O https://raw.githubusercontent.com/TracecatHQ/tracecat/main/.env.example
curl -O https://raw.githubusercontent.com/TracecatHQ/tracecat/main/Caddyfile

chmod +x env.sh
./env.sh
docker-compose up -d
