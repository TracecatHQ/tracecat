#!/bin/bash

# Function to display usage
usage() {
    echo "Usage: $0 [options] [new_version]"
    echo "Options:"
    echo "  --major      # Increment major version (1.2.3 -> 2.0.0)"
    echo "  --minor      # Increment minor version (1.2.3 -> 1.3.0)"
    echo "  --beta       # Create or increment beta version (1.2.3 -> 1.2.3-beta.0, 1.2.3-beta.0 -> 1.2.3-beta.1)"
    echo "  --rc         # Create or increment release candidate (1.2.3 -> 1.2.3-rc.0, 1.2.3-rc.0 -> 1.2.3-rc.1)"
    echo "  --release    # Strip prerelease suffix (1.2.3-beta.1 -> 1.2.3)"
    echo "Examples:"
    echo "  $0           # Automatically increment patch version (or prerelease if current is prerelease)"
    echo "  $0 --major   # Increment major version"
    echo "  $0 --minor   # Increment minor version"
    echo "  $0 --beta    # Create or increment beta version"
    echo "  $0 --rc      # Create or increment release candidate"
    echo "  $0 --release # Strip prerelease suffix for stable release"
    echo "  $0 1.0.1     # Set specific version"
    echo "  $0 1.0.0-beta.0  # Set specific prerelease version"
    exit 1
}

# Extract current version from __init__.py
INIT_FILE="tracecat/__init__.py"
if [ ! -f "$INIT_FILE" ]; then
    echo "Error: Cannot find $INIT_FILE"
    exit 1
fi

# Match both regular semver (1.2.3) and prerelease versions (1.2.3-beta.0, 1.2.3-rc.1)
CURRENT_VERSION=$(grep -E '__version__ = "[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z]+\.[0-9]+)?"' "$INIT_FILE" | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z]+\.[0-9]+)?')
if [ -z "$CURRENT_VERSION" ]; then
    echo "Error: Could not extract version from $INIT_FILE"
    exit 1
fi

# Parse version components
if [[ $CURRENT_VERSION =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)(-([a-zA-Z]+)\.([0-9]+))?$ ]]; then
    CURRENT_MAJOR="${BASH_REMATCH[1]}"
    CURRENT_MINOR="${BASH_REMATCH[2]}"
    CURRENT_PATCH="${BASH_REMATCH[3]}"
    CURRENT_PRERELEASE_TAG="${BASH_REMATCH[5]}"  # e.g., "beta", "rc"
    CURRENT_PRERELEASE_NUM="${BASH_REMATCH[6]}"  # e.g., "0", "1"
else
    echo "Error: Could not parse version components from $CURRENT_VERSION"
    exit 1
fi

# Parse arguments and determine new version
if [ "$#" -eq 0 ]; then
    # Auto-increment: if prerelease, increment prerelease number; otherwise increment patch
    if [ -n "$CURRENT_PRERELEASE_TAG" ]; then
        NEW_VERSION="${CURRENT_MAJOR}.${CURRENT_MINOR}.${CURRENT_PATCH}-${CURRENT_PRERELEASE_TAG}.$((CURRENT_PRERELEASE_NUM + 1))"
        echo "No version specified. Incrementing prerelease version to $NEW_VERSION"
    else
        NEW_VERSION="${CURRENT_MAJOR}.${CURRENT_MINOR}.$((CURRENT_PATCH + 1))"
        echo "No version specified. Incrementing patch version to $NEW_VERSION"
    fi
elif [ "$#" -eq 1 ]; then
    case $1 in
        --major)
            # Increment major, reset minor and patch to 0, strip prerelease
            NEW_VERSION="$((CURRENT_MAJOR + 1)).0.0"
            echo "Incrementing major version to $NEW_VERSION"
            ;;
        --minor)
            # Increment minor, reset patch to 0, strip prerelease
            NEW_VERSION="${CURRENT_MAJOR}.$((CURRENT_MINOR + 1)).0"
            echo "Incrementing minor version to $NEW_VERSION"
            ;;
        --beta)
            # Create or increment beta version
            if [ "$CURRENT_PRERELEASE_TAG" = "beta" ]; then
                NEW_VERSION="${CURRENT_MAJOR}.${CURRENT_MINOR}.${CURRENT_PATCH}-beta.$((CURRENT_PRERELEASE_NUM + 1))"
                echo "Incrementing beta version to $NEW_VERSION"
            else
                # Strip any existing prerelease and start beta.0
                NEW_VERSION="${CURRENT_MAJOR}.${CURRENT_MINOR}.${CURRENT_PATCH}-beta.0"
                echo "Creating beta version $NEW_VERSION"
            fi
            ;;
        --rc)
            # Create or increment release candidate
            if [ "$CURRENT_PRERELEASE_TAG" = "rc" ]; then
                NEW_VERSION="${CURRENT_MAJOR}.${CURRENT_MINOR}.${CURRENT_PATCH}-rc.$((CURRENT_PRERELEASE_NUM + 1))"
                echo "Incrementing release candidate to $NEW_VERSION"
            else
                # Strip any existing prerelease and start rc.0
                NEW_VERSION="${CURRENT_MAJOR}.${CURRENT_MINOR}.${CURRENT_PATCH}-rc.0"
                echo "Creating release candidate $NEW_VERSION"
            fi
            ;;
        --release)
            # Strip prerelease suffix for stable release
            if [ -n "$CURRENT_PRERELEASE_TAG" ]; then
                NEW_VERSION="${CURRENT_MAJOR}.${CURRENT_MINOR}.${CURRENT_PATCH}"
                echo "Stripping prerelease suffix for stable release $NEW_VERSION"
            else
                echo "Error: Current version $CURRENT_VERSION is already a stable release"
                exit 1
            fi
            ;;
        --help|-h)
            usage
            ;;
        *)
            # Assume it's a specific version
            NEW_VERSION=$1
            ;;
    esac
else
    usage
fi

# Validate version numbers (semver format with optional prerelease)
if ! [[ $NEW_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z]+\.[0-9]+)?$ ]]; then
    echo "Error: Version must be in semver format (e.g., 1.0.0 or 1.0.0-beta.0)"
    exit 1
fi

# List of files to update (hardcoded)
FILES=(
    "tracecat/__init__.py"
    "packages/tracecat-registry/tracecat_registry/__init__.py"
    "docker-compose.yml"
    "docker-compose.dev.yml"
    "docker-compose.local.yml"
    "deployments/helm/tracecat/Chart.yaml"
    "deployments/terraform/aws/variables.tf"
    "deployments/terraform/aws/modules/eks/variables.tf"
    "deployments/fargate/variables.tf"
    "deployments/fargate/modules/ecs/variables.tf"
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
    # Escape special characters in version strings for sed
    local ESCAPED_CURRENT=$(echo "$CURRENT_VERSION" | sed 's/[.]/\\./g; s/-/\\-/g')
    local ESCAPED_NEW=$(echo "$NEW_VERSION" | sed 's/[&/]/\\&/g')

    local basename=$(basename "$file")

    # File-specific update strategies
    if [[ "$basename" == "Chart.yaml" ]]; then
        # Targeted: update the appVersion field regardless of its current value
        if [[ "$(uname)" == "Darwin" ]]; then
            sed -i '' -E 's/^(appVersion: ).*/\1"'"$NEW_VERSION"'"/' "$file" && \
            echo "✓ Updated $file" || echo "✗ Failed to update $file"
        else
            sed -i -E 's/^(appVersion: ).*/\1"'"$NEW_VERSION"'"/' "$file" && \
            echo "✓ Updated $file" || echo "✗ Failed to update $file"
        fi
    elif [[ "$basename" == "variables.tf" ]]; then
        # Targeted: update the tracecat_image_tag variable's default value
        if [[ "$(uname)" == "Darwin" ]]; then
            sed -i '' -E '/variable "tracecat_image_tag"/,/\}/ s/(default[[:space:]]*=[[:space:]]*)"[^"]*"/\1"'"$NEW_VERSION"'"/' "$file" && \
            echo "✓ Updated $file" || echo "✗ Failed to update $file"
        else
            sed -i -E '/variable "tracecat_image_tag"/,/\}/ s/(default[[:space:]]*=[[:space:]]*)"[^"]*"/\1"'"$NEW_VERSION"'"/' "$file" && \
            echo "✓ Updated $file" || echo "✗ Failed to update $file"
        fi
    else
        # Default: generic version string find-and-replace
        if [[ "$(uname)" == "Darwin" ]]; then
            # Update version numbers in various formats:
            # - Regular version strings (including prerelease)
            # - Git commit references in URLs (including prerelease)
            # - Version references in code examples and text (including prerelease)
            sed -i '' -E "s/$ESCAPED_CURRENT/$ESCAPED_NEW/g" "$file" && \
            sed -i '' -E "s/\/blob\/[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z]+\.[0-9]+)?\//\/blob\/$NEW_VERSION\//g" "$file" && \
            sed -i '' -E "s/\`[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z]+\.[0-9]+)?\`/\`$NEW_VERSION\`/g" "$file" && \
            echo "✓ Updated $file" || echo "✗ Failed to update $file"
        else
            # Update version numbers in various formats:
            # - Regular version strings (including prerelease)
            # - Git commit references in URLs (including prerelease)
            # - Version references in code examples and text (including prerelease)
            sed -i -E "s/$ESCAPED_CURRENT/$ESCAPED_NEW/g" "$file" && \
            sed -i -E "s/\/blob\/[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z]+\.[0-9]+)?\//\/blob\/$NEW_VERSION\//g" "$file" && \
            sed -i -E "s/\`[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z]+\.[0-9]+)?\`/\`$NEW_VERSION\`/g" "$file" && \
            echo "✓ Updated $file" || echo "✗ Failed to update $file"
        fi
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

# Copy new version to clipboard
if command -v pbcopy &> /dev/null; then
    echo -n "$NEW_VERSION" | pbcopy
    echo -e "\033[36mNew version ($NEW_VERSION) copied to clipboard!\033[0m"
elif command -v xclip &> /dev/null; then
    echo -n "$NEW_VERSION" | xclip -selection clipboard
    echo -e "\033[36mNew version ($NEW_VERSION) copied to clipboard!\033[0m"
elif command -v wl-copy &> /dev/null; then
    echo -n "$NEW_VERSION" | wl-copy
    echo -e "\033[36mNew version ($NEW_VERSION) copied to clipboard!\033[0m"
else
    echo -e "\033[33mClipboard functionality not available (pbcopy/xclip/wl-copy not found)\033[0m"
fi
