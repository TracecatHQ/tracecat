#!/bin/bash

# Redirect all output to a log file
exec > >(tee /var/log/user-data.log) 2>&1

# Install the SSM agent
yum update -y
yum install -y amazon-ssm-agent
systemctl enable amazon-ssm-agent
systemctl start amazon-ssm-agent

# Default branch/tag
echo "Starting user data script execution"
echo "Using Tracecat version: ${tracecat_version}"

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
curl -O "https://raw.githubusercontent.com/TracecatHQ/tracecat/${TRACECAT_TAG}/docker-compose.yml"
curl -O "https://raw.githubusercontent.com/TracecatHQ/tracecat/${TRACECAT_TAG}/env.sh"
curl -O "https://raw.githubusercontent.com/TracecatHQ/tracecat/${TRACECAT_TAG}/.env.example"
curl -O "https://raw.githubusercontent.com/TracecatHQ/tracecat/${TRACECAT_TAG}/Caddyfile"

chmod +x env.sh

# Run the env.sh script in production mode
# and replace http://localhost with http://localhost:8080
printf "y\nlocalhost:8080\n" | ./env.sh

docker-compose up -d
