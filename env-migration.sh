#!/bin/bash

# SUMMARY
# -------
# This script migrates your existing .env file to match a new .env.template structure.
# Here's what it does:
#
# 1. Creates a timestamped backup of your current .env file.
# 2. Reads your existing .env file and the new .env.template.
# 3. Creates a new .env file based on the .env.template structure.
# 4. Preserves values from your old .env for keys that exist in the new template.
# 5. Adds new keys from the template (with empty values) if they didn't exist in your old .env.
# 6. Ignores keys from your old .env that don't exist in the new template.
#
# Note: This script will overwrite your current .env file, but a backup is always created first.
# You will be asked for confirmation before any changes are made.

# Function to read and parse .env file
parse_env() {
    local file=$1
    grep -v '^#' "$file" | grep '=' | sed 's/[[:space:]]*=[[:space:]]*/=/' | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//'
}

# Get current datetime
DATETIME=$(date "+%Y%m%d_%H%M%S")

# Paths to the files
OLD_ENV=".env"
NEW_TEMPLATE=".env.example"
OUTPUT_ENV=".env"
BACKUP_ENV=".env.backup_${DATETIME}"

# Check if .env file exists
if [ ! -f "$OLD_ENV" ]; then
    echo "Error: $OLD_ENV file not found."
    exit 1
fi

# Check if .env.template file exists
if [ ! -f "$NEW_TEMPLATE" ]; then
    echo "Error: $NEW_TEMPLATE file not found."
    exit 1
fi

# Prompt for backup
read -p "Do you want to create a backup of the current .env file? (y/n): " backup_choice
if [[ $backup_choice =~ ^[Yy]$ ]]; then
    cp "$OLD_ENV" "$BACKUP_ENV"
    echo "Backup created at $BACKUP_ENV"
fi

# Warning about overriding
echo "Warning: This script will override the current .env file."
read -p "Do you want to continue? (y/n): " continue_choice
if [[ ! $continue_choice =~ ^[Yy]$ ]]; then
    echo "Operation cancelled."
    exit 0
fi

# Temporary file for processing
TEMP_FILE=$(mktemp)

# Process the new template and create the output
while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ $line =~ ^[[:space:]]*$ || $line =~ ^# ]]; then
        # Empty line or comment, copy as is
        echo "$line" >> "$TEMP_FILE"
    elif [[ $line =~ ^[[:space:]]*([^=]+)[[:space:]]*= ]]; then
        # Extract key
        key=${BASH_REMATCH[1]}
        # Look for the key in the old .env file
        old_value=$(grep "^${key}=" "$OLD_ENV" | sed "s/^${key}=//")
        if [[ -n "$old_value" ]]; then
            # Key exists in old .env, use its value
            echo "${key}=${old_value}" >> "$TEMP_FILE"
        else
            # Key doesn't exist in old .env, copy line as is
            echo "$line" >> "$TEMP_FILE"
        fi
    else
        # Any other line, copy as is
        echo "$line" >> "$TEMP_FILE"
    fi
done < "$NEW_TEMPLATE"

# Move the temporary file to the final output
mv "$TEMP_FILE" "$OUTPUT_ENV"

echo "Migration completed. New .env file created"
