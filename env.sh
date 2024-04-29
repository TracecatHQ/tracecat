#!/bin/bash

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

echo -e "${YELLOW}Creating .env...${NC}"

# Check that docker and ngrok exist
if ! command -v docker &> /dev/null
then
    echo -e "${RED}Docker could not be found. Please install Docker and try again.${NC}"
    exit
fi

if ! command -v ngrok &> /dev/null
then
    echo -e "${RED}Ngrok could not be found. Please install Ngrok and try again.${NC}"
    exit
fi

# If .env exists, ask user if they want to overwrite it
if [ -f .env ]; then
    read -p "A .env file already exists. Do you want to overwrite it? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Exiting...${NC}"
        exit 0
    fi
fi

# Create .env file
runner_url=""
env_file=".env"

# take inputs
# Runner URL
# Prompt the user for the runner URL, use stdin
echo -e "${BLUE}We recommend using ngrok https://ngrok.com/ to set up a static domain for your Runner URL.${NC}"
echo -ne "${BLUE}Enter the Runner URL (required, e.g., https://your-ngrok-static-domain.ngrok-free.app):${NC}"

read runner_url

# Runner integrations
# OpenAI API key
echo -ne "${BLUE}Enter your OpenAI API key to use AI functionality (optional, press Enter to skip):${NC}"
read -s openai_api_key
echo

# Resend API key
echo -ne "${BLUE}Enter your Resend API key to use Email functionality (optional, press Enter to skip):${NC}"
read -s resend_api_key
echo

echo -e "${YELLOW}Generating new service key and signing secret...${NC}"
service_key=$(openssl rand -hex 32)
signing_secret=$(openssl rand -hex 32)


echo -e "${YELLOW}Generating a Fernet encryption key for the database...${NC}"
db_fernet_key=$(docker run --rm python:3.12-slim-bookworm /bin/bash -c "\
    pip install cryptography >/dev/null 2>&1; \
    python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")

echo -e "${YELLOW}Creating new .env from .env.example...${NC}"
cp .env.example .env
# Replace existing values of TRACECAT__SERVICE_KEY and TRACECAT__SIGNING_SECRET
dotenv_replace "TRACECAT__SERVICE_KEY" "$service_key" "$env_file"
dotenv_replace "TRACECAT__SIGNING_SECRET" "$signing_secret" "$env_file"
dotenv_replace "TRACECAT__DB_ENCRYPTION_KEY" "$db_fernet_key" "$env_file"
dotenv_replace "TRACECAT__RUNNER_URL" "$runner_url" "$env_file"
dotenv_replace "OPENAI_API_KEY" "$openai_api_key" "$env_file"
dotenv_replace "RESEND_API_KEY" "$resend_api_key" "$env_file"

# Check if the existing value matches the default value
if [ "$runner_url" == "https://your-ngrok-runner-url" ]; then
    echo -e "${RED}The TRACECAT__RUNNER_URL value is missing. Please update it in the .env file.${NC}"
    exit 1
fi
