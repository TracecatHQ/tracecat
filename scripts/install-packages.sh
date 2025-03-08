#!/bin/bash

# Bash "strict mode", to help catch problems and bugs in the shell
# script. Every bash script you write should include this. See
# http://redsymbol.net/articles/unofficial-bash-strict-mode/ for details.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# Update package lists
apt-get update

# Install base packages
apt-get install -y \
  acl \
  git \
  xmlsec1

# Apply security updates
apt-get -y upgrade

# Clean up
apt-get clean
rm -rf /var/lib/apt/lists/*
