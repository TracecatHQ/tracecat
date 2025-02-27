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

# Check that docker exists and is running
if !  docker ps &> /dev/null
then
    echo -e "${RED}Docker could not be found. Please check if installed and running.${NC}"
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

if [ ! -e ".env.example" ] ; then
  echo "${RED}No .env.example file found in current directory: $(pwd). Please download .env.example from the Tracecat GitHub repo and rerun the env.sh script."
  exit 1
fi
env_file=".env"

if ! openssl --help &> /dev/null
then
  echo  -e "${RED}Could not run openssl. Please check if openssl is correctly installed."
  exit 1
fi

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


# Prompt user for environment mode
while true; do
    read -p "Use production mode? (y/n, default: y): " prod_mode
    prod_mode=${prod_mode:-y}
    case $prod_mode in
        [Yy]* )
            env_mode="production"
            break
            ;;
        [Nn]* )
            env_mode="development"
            break
            ;;
        * ) echo -e "${RED}Please answer y or n.${NC}";;
    esac
done

# Prompt user for new IP address and strip http:// or https://

while true; do
    read -p "Enter the IP address or domain the server should listen on (default: localhost): " new_ip
    new_ip=$(sed -E 's/^\s*.*:\/\///g' <<< $new_ip)
    new_ip=${new_ip:-localhost}

    if [ "$new_ip" != "0.0.0.0" ]; then
        break
    fi
    echo -e "${RED}Cannot use 0.0.0.0 as address. You need to enter external IP address / domain name of your server (or use localhost)\nSee https://docs.tracecat.com/self-hosting/deployment-options/docker-compose#download-configuration-files ${NC}"
done


# Prompt user for PostgreSQL SSL mode
while true; do
    read -p "Require PostgreSQL SSL mode? (y/n, default: n): " postgres_ssl
    postgres_ssl=${postgres_ssl:-n}
    case $postgres_ssl in
        [Yy]* )
            ssl_mode="require"
            break
            ;;
        [Nn]* )
            ssl_mode="disable"
            break
            ;;
        * ) echo -e "${RED}Please answer y or n.${NC}";;
    esac
done

# Update environment variables
dotenv_replace "TRACECAT__APP_ENV" "$env_mode" "$env_file"
dotenv_replace "NODE_ENV" "$env_mode" "$env_file"
dotenv_replace "NEXT_PUBLIC_APP_ENV" "$env_mode" "$env_file"
dotenv_replace "PUBLIC_API_URL" "http://${new_ip}/api/" "$env_file"
dotenv_replace "PUBLIC_APP_URL" "http://${new_ip}" "$env_file"
dotenv_replace "TRACECAT__DB_SSLMODE" "$ssl_mode" "$env_file"

# Remove duplicate entries and leading/trailing commas
new_origins=$(echo "$new_origins" | tr ',' '\n' | sort -u | tr '\n' ',' | sed 's/^,//;s/,$//')
dotenv_replace "TRACECAT__ALLOW_ORIGINS" "$new_origins" "$env_file"

echo -e "${GREEN}Environment file created successfully.${NC}"
