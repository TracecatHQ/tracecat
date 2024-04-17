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

# Start services flags
DOCKER_COMPOSE_UP_FLAGS=
DOCKER_COMPOSE_COMMAND=
TAIL_LOGS=false
BUILD_SERVICES=false
ENVIRONMENT="dev"
REBUILD_NO_CACHE=false

# Stop services flags
# Remove all by default
REMOVE_INTERNAL=true
REMOVE_SUPABASE=true

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

set_docker_compose_command() {
    case $ENVIRONMENT in
        dev)
            DOCKER_COMPOSE_COMMAND="docker compose"
            ;;
        prod)
            DOCKER_COMPOSE_COMMAND="docker compose -f docker-compose.yaml -f docker-compose.prod.yaml"
            ;;
        *)
            echo -e "${RED}Invalid environment '${ENVIRONMENT}' specified. Please use 'dev' or 'prod'.${NC}"
            exit 1
            ;;
    esac
    echo -e "${YELLOW}Using command '$DOCKER_COMPOSE_COMMAND'.${NC}"
}

# Function to handle start command
start_services() {
    # Check for --tail flag in subsequent arguments. If not present, start services in detached mode.
    if ! $TAIL_LOGS; then
        DOCKER_COMPOSE_UP_FLAGS+=" --detach"
    fi

    # Check for --build flag in subsequent arguments
    if $BUILD_SERVICES; then
        DOCKER_COMPOSE_UP_FLAGS+=" --build"
    fi

    echo -e "${YELLOW}Starting Tracecat application setup...${NC}"

    runner_url=""
    env_file=".env"
    # Check if .env file exists, if not, create from .env.example
    if [ ! -f .env ]; then
        echo -e "${YELLOW}No .env file detected. Running setup.${NC}"
        if ! command -v python >/dev/null 2>&1 && ! command -v python3 >/dev/null 2>&1; then
            echo -e "${RED}Python is required to generate the Fernet key in the setup.${NC}"
            exit 1
        fi

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
        python -m pip install cryptography >/dev/null 2>&1
        db_fernet_key=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")



        echo -e "${YELLOW}Creating new .env from .env.example...${NC}"
        cp .env.example .env
        # Replace existing values of TRACECAT__SERVICE_KEY and TRACECAT__SIGNING_SECRET
        dotenv_replace "TRACECAT__SERVICE_KEY" "$service_key" "$env_file"
        dotenv_replace "TRACECAT__SIGNING_SECRET" "$signing_secret" "$env_file"
        dotenv_replace "TRACECAT__DB_ENCRYPTION_KEY" "$db_fernet_key" "$env_file"
        dotenv_replace "TRACECAT__RUNNER_URL" "$runner_url" "$env_file"
        dotenv_replace "OPENAI_API_KEY" "$openai_api_key" "$env_file"
        dotenv_replace "RESEND_API_KEY" "$resend_api_key" "$env_file"
    fi


    # Extract the value of TRACECAT__RUNNER_URL from the .env file
    runner_url=$(grep "^TRACECAT__RUNNER_URL=" "$env_file" | cut -d'=' -f2)

    # Check if the existing value matches the default value
    if [ "$runner_url" == "https://your-ngrok-runner-url" ]; then
        echo -e "${RED}The TRACECAT__RUNNER_URL value is missing. Please update it in the .env file.${NC}"
        exit 1
    fi
    # Check if Docker is running
    if echo "$output" | grep -q "Cannot connect to the Docker daemon"; then
        echo -e "${RED}Docker is not running. Please start Docker and try again.${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Initializing Supabase services...${NC}"
    output=$(supabase start 2>&1)


    # Check for errors or if Supabase is already running
    if echo "$output" | grep -q "error"; then
        echo -e "${RED}Error encountered while starting Supabase:${NC}"
        echo "$output" | grep "error"  # Display only the error message, not full output
        exit 1
    elif echo "$output" | grep -q "supabase start is already running"; then
        echo -e "${YELLOW}Supabase is already running. Proceeding with the current session...${NC}"
        output=$(supabase status)  # Capturing the status for potential use
    fi

    # Attempt to extract the anonymous key without displaying it
    if [[ "$OSTYPE" == "darwin"* ]]; then
        anon_key=$(echo "$output" | grep -o 'anon key: [^ ]*' | cut -d ' ' -f 3 || true)
    else
        anon_key=$(echo "$output" | grep -oP 'anon key: \K\S+' || true)
    fi

    if [ -z "$anon_key" ]; then
        echo -e "${RED}Could not extract the anonymous key from Supabase.${NC}"
        exit 1
    else
        dotenv_replace "NEXT_PUBLIC_SUPABASE_ANON_KEY" "$anon_key" ".env"
        echo -e "${GREEN}Anonymous key successfully extracted and added to the .env file.${NC}"
    fi

    # 1. Stop the services if they are already running
    if $DOCKER_COMPOSE_COMMAND down --remove-orphans; then
        echo -e "${GREEN}Tracecat services stopped successfully.${NC}"
    else
        echo -e "${RED}Failed to stop Tracecat services. Please check the logs for more details.${NC}"
        exit 1
    fi

    # 2. Rebuild the services if the --build flag is set
    if $REBUILD_NO_CACHE; then
        if $DOCKER_COMPOSE_COMMAND build --no-cache; then
            echo -e "${GREEN}Tracecat services rebuilt successfully.${NC}"
        else
            echo -e "${RED}Failed to rebuild Tracecat services. Please check the logs for more details.${NC}"
            exit 1
        fi
    fi

    # 3. Start the services
    echo -e "${YELLOW}Starting Tracecat services with docker compose flags '$DOCKER_COMPOSE_UP_FLAGS'.${NC}"
    if  $DOCKER_COMPOSE_COMMAND up $DOCKER_COMPOSE_UP_FLAGS; then
        echo -e "${GREEN}Tracecat local development setup started successfully.${NC}"
        echo -e "${BLUE}API URL:${NC} http://localhost:8000"
        echo -e "${BLUE}Runner URL:${NC} http://localhost:8001"
        echo -e "${BLUE}Frontend URL:${NC} http://localhost:3000"
        echo -e "${BLUE}External Runner URL:${NC} $runner_url"
    else
        echo -e "${RED}Failed to restart Tracecat services. Please check the logs for more details.${NC}"
        exit 1
    fi
    echo -e "${GREEN}Tracecat local development setup is complete.${NC}"
}


# Function to handle stop command
stop_services() {
    if [ $REMOVE_INTERNAL = true ]; then
        echo -e "${YELLOW}Stopping Tracecat services...${NC}"
        if docker compose down --remove-orphans; then
            echo -e "${GREEN}Tracecat services stopped successfully.${NC}"
        else
            echo -e "${RED}Failed to stop Tracecat services. Please check the logs for more details.${NC}"
        fi
    fi
    if [ $REMOVE_SUPABASE = true ]; then
        echo -e "${YELLOW}Stopping Supabase services...${NC}"
        if supabase stop; then
            echo -e "${GREEN}Supabase services stopped successfully.${NC}"
        else
            echo -e "${RED}Failed to stop Supabase services. Please check the logs for more details.${NC}"
        fi
    fi
}



# Parse the first command-line argument for the action
ACTION=$1
shift # Remove the first argument, leaving any additional arguments

# Execute based on the action
case $ACTION in
    start)
        while (( "$#" )); do
            case "$1" in
                -t | --tail ) TAIL_LOGS=true; shift ;;
                -b | --build ) BUILD_SERVICES=true; shift ;;
                -n | --no-cache ) REBUILD_NO_CACHE=true; shift ;;
                -e | --env ) ENVIRONMENT="$2"; shift 2 ;;
                * ) echo -e "${RED}Unknown flag '$1', Stopping.${NC}"; exit 1 ;;
            esac
        done
        echo -e "${YELLOW}Environment: $ENVIRONMENT${NC}"
        set_docker_compose_command
        start_services
        ;;
    stop)
        while (( "$#" )); do
            case "$1" in
                -s | --supabase ) REMOVE_INTERNAL=false; shift ;;
                -i | --internal ) REMOVE_SUPABASE=false; shift ;;
                -e | --env ) ENVIRONMENT="$2"; shift 2 ;;
                * ) echo -e "${RED}Unknown flag '$1', Stopping.${NC}"; exit 1 ;;
            esac
        done
        echo -e "${YELLOW}Environment: $ENVIRONMENT${NC}"
        set_docker_compose_command
        stop_services
        ;;
    *)
        echo -e "${RED}Usage: $0 {start|stop} [--tail|-t]${NC}"
        exit 1
        ;;
esac
