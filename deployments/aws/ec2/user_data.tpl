#!/bin/bash

# Redirect all output to a log file
exec > >(tee /var/log/user-data.log) 2>&1

# Install the SSM agent
yum update -y
yum install -y amazon-ssm-agent
systemctl enable amazon-ssm-agent
systemctl start amazon-ssm-agent

# Install EFS utils
yum install -y amazon-efs-utils

# Mount EFS
mkdir -p /mnt/efs
mount -t efs -o tls ${efs_id}:/ /mnt/efs

# Add EFS mount to /etc/fstab for persistence across reboots
echo "${efs_id}:/ /mnt/efs efs defaults,_netdev 0 0" >> /etc/fstab

# Create directories for data and config
mkdir -p /mnt/efs/core-db /mnt/efs/temporal-db /mnt/efs/config

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
curl -O "https://raw.githubusercontent.com/TracecatHQ/tracecat/${tracecat_version}/docker-compose.yml"
curl -O "https://raw.githubusercontent.com/TracecatHQ/tracecat/${tracecat_version}/env.sh"
curl -O "https://raw.githubusercontent.com/TracecatHQ/tracecat/${tracecat_version}/.env.example"
curl -O "https://raw.githubusercontent.com/TracecatHQ/tracecat/${tracecat_version}/Caddyfile"

chmod +x env.sh

# Run env.sh only if .env doesn't exist in EFS
if [ ! -f /mnt/efs/config/.env ]; then
    printf "y\nlocalhost:8080\nn\n" | ./env.sh
    mv .env /mnt/efs/config/.env
fi

# Create symlink to .env in EFS
ln -sf /mnt/efs/config/.env /home/ec2-user/.env

# Update docker-compose.yml to use EFS paths
sed -i 's|- core-db:/var/lib/postgresql/data|- /mnt/efs/core-db:/var/lib/postgresql/data|g' docker-compose.yml
sed -i 's|- temporal-db:/var/lib/postgresql/data|- /mnt/efs/temporal-db:/var/lib/postgresql/data|g' docker-compose.yml

# Start Docker Compose
docker-compose up -d