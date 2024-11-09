#!/bin/bash

# Function to display usage
usage() {
    echo "Usage: $0 <current_version> <new_version>"
    echo "Example: $0 1.0.0 1.0.1"
    exit 1
}

# Check if we have the required arguments
if [ "$#" -ne 2 ]; then
    usage
fi

CURRENT_VERSION=$1
NEW_VERSION=$2

# Validate version numbers (basic semver format)
if ! [[ $CURRENT_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || ! [[ $NEW_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: Versions must be in semver format (e.g., 1.0.0)"
    exit 1
fi

# List of files to update (hardcoded)
FILES=(
    "tracecat/__init__.py"
    "pyproject.toml"
    "docker-compose.yml"
    "docs/tutorials/updating.mdx"
    "docs/self-hosting/deployment-options/docker-compose.mdx"
    "deployments/aws/fargate/variables.tf"
)

# Function to update version in a file
update_version() {
    local file=$1

    if [ ! -f "$file" ]; then
        echo "Warning: File not found - $file"
        return
    fi

    echo "Updating $file..."
    # On MacOS, sed requires a different syntax for in-place editing
    if [[ "$(uname)" == "Darwin" ]]; then
        sed -i '' "s/$CURRENT_VERSION/$NEW_VERSION/g" "$file" && echo "✓ Updated $file" || echo "✗ Failed to update $file"
    else
        sed -i "s/$CURRENT_VERSION/$NEW_VERSION/g" "$file" && echo "✓ Updated $file" || echo "✗ Failed to update $file"
    fi
}

# Main execution
echo "Updating version from $CURRENT_VERSION to $NEW_VERSION"
echo "The following files will be modified:"
echo "----------------------------------------"
for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "  - $file"
    else
        echo "  - $file (not found)"
    fi
done
echo "----------------------------------------"

# Ask for confirmation
read -p "Do you want to proceed with these changes? (y/N) " -n 1 -r
echo    # Move to a new line

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Operation cancelled."
    exit 1
fi

# Proceed with updates
echo "Proceeding with updates..."
for file in "${FILES[@]}"; do
    update_version "$file"
done

echo "----------------------------------------"
echo -e "\033[32mVersion update complete!\033[0m"
echo -e "\033[33mPlease review the changes before committing.\033[0m"
