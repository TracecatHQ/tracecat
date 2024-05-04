#!/bin/bash
# Usage:
# 1. run:
# chmod +x env.traefik.sh && ./env.traefik.sh

# 2. set up your dns for tracecat, tc-api, tc-runner in your local hosts file or cloudflare

# 3. run (cert will appear as traefik default for 5 minutes at first): 
# docker compose -f docker-compose.traefik.yaml --env-file .env.traefik up -d

# FUNCTIONS
# Define color codes
if command -v tput >/dev/null && [ -t 1 ]; then
    RED=$(tput setaf 1)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    BLUE=$(tput setaf 4)
    NC=$(tput sgr0) # No Color
else
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    NC='\033[0m' # No Color
fi

dotenv_replace() {
    local env_var_name=$1
    local new_value=$2
    local file_path=$3
    local sed_option=""

    # Check if running on macOS and adjust sed_option accordingly
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed_option="-i ''"
    else
        sed_option="-i"
    fi

    # Use eval to correctly handle the dynamic insertion of the sed option
    delimiter="#"
    eval sed $sed_option "s$delimiter^${env_var_name}=.*$delimiter${env_var_name}=${new_value}$delimiter" $file_path
}

env_file='.env.traefik'
# Append traefik vars to current env.

# Cloudflare Email
echo -ne "${BLUE}Enter your Cloudflare email address (press enter to skip):${NC}"
read CLOUDFLARE_EMAIL
echo

# Cloudflare Key
echo -ne "${BLUE}Enter your Cloudflare API key (https://developers.cloudflare.com/fundamentals/api/get-started/create-token/) (press enter to skip and manually set up traefik provider):${NC}"
read -s CLOUDFLARE_API_KEY
echo

# Root Domain
echo -ne "${BLUE}Enter your root domain (tracecat.com):${NC}"
read ROOT_DOMAIN
echo

BASIC_AUTH_DEFAULT='$2a$12$NRftuBb4WjHvJahzfZz0reAy4HU.iBvCmzynQMRMv50al2u8nVwCG'

echo -e "
# --- Required Traefik Variables ---
# Refer to https://doc.traefik.io/traefik/https/acme/#providers for more DNS verification methods
CLOUDFLARE_EMAIL=${CLOUDFLARE_EMAIL}
CLOUDFLARE_API_KEY=${CLOUDFLARE_API_KEY}
DOCKERDIR=.
ROOT_DOMAIN=${ROOT_DOMAIN}

# --- Optional Traefik Dashboard ---
# Optionally used for traefik dashboard behind basic auth using https://bcrypt-generator.com/ (admin//admin)
DASHBOARD=false
BASIC_AUTH_USER=admin
BASIC_AUTH_PASS='${BASIC_AUTH_DEFAULT}'

$(cat .env)
" > $env_file

# Replacements
dotenv_replace "TRACECAT__RUNNER_URL" 'https://tc-runner.\${ROOT_DOMAIN}' "$env_file"
dotenv_replace "NEXT_PUBLIC_APP_URL" 'https://tracecat.\${ROOT_DOMAIN}' "$env_file"
dotenv_replace "NEXT_PUBLIC_API_URL" 'https://tc-api.\${ROOT_DOMAIN}' "$env_file"
dotenv_replace "TRACECAT__APP_ENV" "production" "$env_file"

echo -ne "${GREEN}Ensure your records are set up for tc-runner, tracecat, tc-api in your ${ROOT_DOMAIN} DNS provider (localhost or cloudflare)"
echo -ne "${GREEN}run: docker compose -f docker-compose.traefik.yaml --env-file .env.traefik up"
echo -ne "${YELLOW}please note: you must wait until your certificate is acquired from traefik ACME service (about 3 minutes) before tracecat will function properly"