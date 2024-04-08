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

echo -e "${YELLOW}Starting Tracecat application setup...${NC}"

# Check if .env file exists, if not, create from .env.example
if [ ! -f .env ]; then
    echo -e "${YELLOW}No .env file detected. Creating one from the .env.example...${NC}"
    cp .env.example .env
    # Replace existing values of TRACECAT__SERVICE_KEY and TRACECAT__SIGNING_SECRET
    sed -i "s/^TRACECAT__SERVICE_KEY=.*/TRACECAT__SERVICE_KEY=$(openssl rand -hex 32)/" .env
    sed -i "s/^TRACECAT__SIGNING_SECRET=.*/TRACECAT__SIGNING_SECRET=$(openssl rand -hex 32)/" .env
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
anon_key=$(echo "$output" | grep -oP 'anon key: \K\S+' || true)
if [ -z "$anon_key" ]; then
    echo -e "${RED}Could not extract the anonymous key from Supabase.${NC}"
    exit 1
else
    sed -i "s/^NEXT_PUBLIC_SUPABASE_ANON_KEY=.*/NEXT_PUBLIC_SUPABASE_ANON_KEY=$anon_key/" .env
    echo -e "${GREEN}Anonymous key successfully extracted and added to the .env file.${NC}"
fi

echo -e "${YELLOW}Building and launching Tracecat services...${NC}"
if docker-compose build && docker-compose up -d; then
    echo -e "${GREEN}Tracecat local development setup started successfully.${NC}"
    echo -e "${BLUE}API URL:${NC} http://localhost:8000"
    echo -e "${BLUE}Runner URL:${NC} http://localhost:8001"
    echo -e "${BLUE}Frontend URL:${NC} http://localhost:3000"
else
    echo -e "${RED}Failed to start Tracecat services. Please check the logs for more details.${NC}"
    exit 1
fi

echo -e "${GREEN}Tracecat local development setup is complete.${NC}"
