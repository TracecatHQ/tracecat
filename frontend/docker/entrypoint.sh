#!/bin/sh

# Update runtime-config.js with environment variables
echo "Updating runtime-config.js with environment variables..."
echo "API_URL: $API_URL"

CONFIG_PATH=/app/public/runtime-config.js

sed -i "s|__PLACEHOLDER_API_URL__|$API_URL|g" $CONFIG_PATH

# Print the updated content of runtime-config.js for debugging
echo "Updated content of $CONFIG_PATH after update:"
cat $CONFIG_PATH

# Pass in the command to run
exec "$@"
