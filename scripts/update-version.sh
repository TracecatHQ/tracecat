#!/bin/bash

# Function to display usage
usage() {
    echo "Usage: $0 [new_version]"
    echo "Examples:"
    echo "  $0           # Automatically increment patch version"
    echo "  $0 1.0.1    # Set specific version"
    exit 1
}

# Extract current version from __init__.py
INIT_FILE="tracecat/__init__.py"
if [ ! -f "$INIT_FILE" ]; then
    echo "Error: Cannot find $INIT_FILE"
    exit 1
fi

CURRENT_VERSION=$(grep -E "__version__ = \"[0-9]+\.[0-9]+\.[0-9]+\"" "$INIT_FILE" | grep -Eo "[0-9]+\.[0-9]+\.[0-9]+")
if [ -z "$CURRENT_VERSION" ]; then
    echo "Error: Could not extract version from $INIT_FILE"
    exit 1
fi

# If no version provided, increment patch version
if [ "$#" -eq 0 ]; then
    # Split version into major.minor.patch
    IFS='.' read -r major minor patch <<< "$CURRENT_VERSION"
    # Increment patch
    NEW_VERSION="${major}.${minor}.$((patch + 1))"
    echo "No version specified. Incrementing patch version to $NEW_VERSION"
elif [ "$#" -eq 1 ]; then
    NEW_VERSION=$1
else
    usage
fi

# Validate version numbers (basic semver format)
if ! [[ $NEW_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: Version must be in semver format (e.g., 1.0.0)"
    exit 1
fi

# List of files to update (hardcoded)
FILES=(
    "tracecat/__init__.py"
    "docker-compose.yml"
    "docs/tutorials/updating.mdx"
    "docs/self-hosting/deployment-options/docker-compose.mdx"
    "docs/quickstart/install.mdx"
    "docs/self-hosting/updating.mdx"
    "CONTRIBUTING.md"
    ".github/ISSUE_TEMPLATE/bug_report.md"
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
        # Update version numbers in various formats:
        # - Regular version strings
        # - Git commit references in URLs
        # - Version references in code examples and text
        sed -i '' -E "s/$CURRENT_VERSION/$NEW_VERSION/g" "$file" && \
        sed -i '' -E "s/\/blob\/[0-9]+\.[0-9]+\.[0-9]+\//\/blob\/$NEW_VERSION\//g" "$file" && \
        sed -i '' -E "s/\`[0-9]+\.[0-9]+\.[0-9]+\`/\`$NEW_VERSION\`/g" "$file" && \
        echo "✓ Updated $file" || echo "✗ Failed to update $file"
    else
        # Update version numbers in various formats:
        # - Regular version strings
        # - Git commit references in URLs
        # - Version references in code examples and text
        sed -i -E "s/$CURRENT_VERSION/$NEW_VERSION/g" "$file" && \
        sed -i -E "s/\/blob\/[0-9]+\.[0-9]+\.[0-9]+\//\/blob\/$NEW_VERSION\//g" "$file" && \
        sed -i -E "s/\`[0-9]+\.[0-9]+\.[0-9]+\`/\`$NEW_VERSION\`/g" "$file" && \
        echo "✓ Updated $file" || echo "✗ Failed to update $file"
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
