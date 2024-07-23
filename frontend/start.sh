#!/bin/sh

# Print environment variables (optional for debugging)
echo "Starting the application with the following environment variables:"
echo "PORT: $PORT"
echo "HOSTNAME: $HOSTNAME"

# Start the application using the standalone server.js
exec node server.js
