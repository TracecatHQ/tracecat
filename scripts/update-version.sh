#!/bin/bash

# Function to display usage
usage() {
    echo "Usage: $0 [options] [new_version]"
    echo "Options:"
    echo "  --major      # Increment major version (1.2.3 -> 2.0.0)"
    echo "  --minor      # Increment minor version (1.2.3 -> 1.3.0)"
    echo "  --beta       # Create or increment beta tag (1.2.3 -> 1.2.3-beta.0, 1.2.3-beta.0 -> 1.2.3-beta.1)"
    echo "  --rc         # Create or increment release candidate tag (1.2.3 -> 1.2.3-rc.0, 1.2.3-rc.0 -> 1.2.3-rc.1)"
    echo "  --release    # Strip prerelease suffix (1.2.3-beta.1 -> 1.2.3)"
    echo "Examples:"
    echo "  $0           # Automatically increment patch version (or prerelease if current is prerelease)"
    echo "  $0 --major   # Increment major version"
    echo "  $0 --minor   # Increment minor version"
    echo "  $0 --beta    # Create or increment beta version"
    echo "  $0 --rc      # Create or increment release candidate"
    echo "  $0 --release # Strip prerelease suffix for stable release"
    echo "  $0 1.0.1     # Set specific version"
    echo "  $0 1.0.0-beta.0  # Set specific prerelease tag"
    echo "  $0 1.0.0-beta.48-rc.5  # Keep release/image tag convention"
    exit 1
}

PUBLIC_SUFFIX_PATTERN='(alpha|a|beta|b|rc|dev|post)\.[0-9]+'
PUBLIC_VERSION_PATTERN="[0-9]+\.[0-9]+\.[0-9]+(-${PUBLIC_SUFFIX_PATTERN}){0,2}"
VERSION_SEARCH_PATTERN='[0-9]+\.[0-9]+\.[0-9]+(-[a-z]+\.[0-9]+){0,2}'

to_python_version() {
    local python_version=$1

    if [[ $python_version =~ ^(.+-[a-z]+\.[0-9]+)-([a-z]+)\.([0-9]+)$ ]]; then
        python_version="${BASH_REMATCH[1]}+${BASH_REMATCH[2]}.${BASH_REMATCH[3]}"
    fi

    python_version=${python_version//-alpha./a}
    python_version=${python_version//-a./a}
    python_version=${python_version//-beta./b}
    python_version=${python_version//-b./b}
    python_version=${python_version//-rc./rc}
    python_version=${python_version//-dev./.dev}
    python_version=${python_version//-post./.post}

    printf '%s\n' "$python_version"
}

escape_version_regex() {
    printf '%s\n' "$1" | sed 's/[.]/\\./g; s/-/\\-/g'
}

escape_sed_replacement() {
    printf '%s\n' "$1" | sed 's/[&/]/\\&/g'
}

# Extract current version from __init__.py
INIT_FILE="tracecat/__init__.py"
REGISTRY_INIT_FILE="packages/tracecat-registry/tracecat_registry/__init__.py"
if [ ! -f "$INIT_FILE" ]; then
    echo "Error: Cannot find $INIT_FILE"
    exit 1
fi

# Match release/image tag versions (1.2.3, 1.2.3-beta.0, 1.2.3-beta.0-rc.1)
CURRENT_VERSION=$(sed -nE "s/^__version__ = \"($PUBLIC_VERSION_PATTERN)\"$/\1/p" "$INIT_FILE")
if [ -z "$CURRENT_VERSION" ]; then
    echo "Error: Could not extract version from $INIT_FILE"
    exit 1
fi

CURRENT_PYTHON_VERSION=$(sed -nE 's/^__pep440_version__ = "([^"]+)"$/\1/p' "$INIT_FILE")
if [ -z "$CURRENT_PYTHON_VERSION" ]; then
    CURRENT_PYTHON_VERSION=$(to_python_version "$CURRENT_VERSION")
fi

# Parse version components
if [[ $CURRENT_VERSION =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)(-.*)?$ ]]; then
    CURRENT_MAJOR="${BASH_REMATCH[1]}"
    CURRENT_MINOR="${BASH_REMATCH[2]}"
    CURRENT_PATCH="${BASH_REMATCH[3]}"
    CURRENT_CORE_VERSION="${CURRENT_MAJOR}.${CURRENT_MINOR}.${CURRENT_PATCH}"
else
    echo "Error: Could not parse version components from $CURRENT_VERSION"
    exit 1
fi

# Parse arguments and determine new version
if [ "$#" -eq 0 ]; then
    # Auto-increment: if prerelease, increment prerelease number; otherwise increment patch
    if [[ $CURRENT_VERSION =~ ^(.+-[a-zA-Z]+\.)([0-9]+)$ ]]; then
        NEW_VERSION="${BASH_REMATCH[1]}$((BASH_REMATCH[2] + 1))"
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
            if [[ $CURRENT_VERSION =~ ^(.+-beta\.)([0-9]+)$ ]]; then
                NEW_VERSION="${BASH_REMATCH[1]}$((BASH_REMATCH[2] + 1))"
                echo "Incrementing beta version to $NEW_VERSION"
            else
                # Strip any existing prerelease and start beta.0
                NEW_VERSION="${CURRENT_CORE_VERSION}-beta.0"
                echo "Creating beta version $NEW_VERSION"
            fi
            ;;
        --rc)
            # Create or increment release candidate
            if [[ $CURRENT_VERSION =~ ^(.+-rc\.)([0-9]+)$ ]]; then
                NEW_VERSION="${BASH_REMATCH[1]}$((BASH_REMATCH[2] + 1))"
                echo "Incrementing release candidate to $NEW_VERSION"
            else
                # Strip any existing prerelease and start rc.0
                NEW_VERSION="${CURRENT_CORE_VERSION}-rc.0"
                echo "Creating release candidate $NEW_VERSION"
            fi
            ;;
        --release)
            # Strip prerelease suffix for stable release
            if [ "$CURRENT_VERSION" != "$CURRENT_CORE_VERSION" ]; then
                NEW_VERSION="$CURRENT_CORE_VERSION"
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

# Validate release/image tag version numbers (semver-style with optional prerelease)
if ! [[ $NEW_VERSION =~ ^${PUBLIC_VERSION_PATTERN}$ ]]; then
    echo "Error: Version must use the release tag format (e.g., 1.0.0, 1.0.0-beta.0, 1.0.0-beta.48-rc.5)"
    exit 1
fi

NEW_PYTHON_VERSION=$(to_python_version "$NEW_VERSION") || {
    echo "Error: Could not convert $NEW_VERSION to a PEP 440-compatible Python package version"
    exit 1
}

# List of files to update
FILES=(
    "tracecat/__init__.py"
    "packages/tracecat-registry/tracecat_registry/__init__.py"
    "CONTRIBUTING.md"
    ".github/ISSUE_TEMPLATE/bug_report.md"
)

append_unique_file() {
    local candidate=$1
    local existing

    for existing in "${FILES[@]}"; do
        if [ "$existing" = "$candidate" ]; then
            return
        fi
    done

    FILES+=("$candidate")
}

append_matching_files() {
    local file

    while IFS= read -r file; do
        [ -n "$file" ] || continue
        append_unique_file "${file#./}"
    done
}

# Capture Tracecat-specific version contexts, including files that only carry the
# current version string rather than an image tag or raw GitHub URL.
append_matching_files < <(
    rg -l \
        -g 'docker-compose*.yml' \
        -g 'docs/**/*' \
        -g 'deployments/**/*' \
        "\\$\\{TRACECAT__IMAGE_TAG:-${VERSION_SEARCH_PATTERN}\\}|variable \"tracecat_image_tag\"|raw\\.githubusercontent\\.com/TracecatHQ/tracecat/${VERSION_SEARCH_PATTERN}/|TF_VAR_tracecat_image_tag=${VERSION_SEARCH_PATTERN}" \
        .
)

append_matching_files < <(
    rg -l --fixed-strings \
        -g 'docker-compose*.yml' \
        -g 'docs/**/*' \
        -g 'deployments/**/*' \
        "$CURRENT_VERSION" \
        .
)

run_sed_in_place() {
    local expression=$1
    local file=$2

    if [[ "$(uname)" == "Darwin" ]]; then
        sed -i '' -E "$expression" "$file"
    else
        sed -i -E "$expression" "$file"
    fi
}

update_python_version_file() {
    local file=$1
    local escaped_new_version
    local escaped_new_python_version

    if [ ! -f "$file" ]; then
        echo "Warning: File not found - $file"
        return
    fi

    echo "Updating $file..."

    escaped_new_version=$(escape_sed_replacement "$NEW_VERSION")
    escaped_new_python_version=$(escape_sed_replacement "$NEW_PYTHON_VERSION")

    run_sed_in_place "s/^(__version__ = \")[^\"]+(\")/\\1${escaped_new_version}\\2/" "$file" && \
    run_sed_in_place "s/^(__pep440_version__ = \")[^\"]+(\")/\\1${escaped_new_python_version}\\2/" "$file" && \
    echo "✓ Updated $file" || echo "✗ Failed to update $file"
}

update_release_tag_file() {
    local file=$1
    local escaped_current_version
    local escaped_new_version

    if [ ! -f "$file" ]; then
        echo "Warning: File not found - $file"
        return
    fi

    echo "Updating $file..."
    escaped_current_version=$(escape_version_regex "$CURRENT_VERSION")
    escaped_new_version=$(escape_sed_replacement "$NEW_VERSION")

    run_sed_in_place "s/$escaped_current_version/$escaped_new_version/g" "$file" && \
    run_sed_in_place "s#(/blob/)${VERSION_SEARCH_PATTERN}/#\\1${escaped_new_version}/#g" "$file" && \
    run_sed_in_place "s/\`${VERSION_SEARCH_PATTERN}\`/\`${escaped_new_version}\`/g" "$file" && \
    run_sed_in_place "s/(\\$\\{TRACECAT__IMAGE_TAG:-)${VERSION_SEARCH_PATTERN}(\\})/\\1${escaped_new_version}\\3/g" "$file" && \
    run_sed_in_place '/variable "tracecat_image_tag"/,/\}/ s/(default[[:space:]]*=[[:space:]]*)"[^"]*"/\1"'"$escaped_new_version"'"/' "$file" && \
    run_sed_in_place "s#(raw\\.githubusercontent\\.com/TracecatHQ/tracecat/)${VERSION_SEARCH_PATTERN}/#\\1${escaped_new_version}/#g" "$file" && \
    run_sed_in_place "s/(TF_VAR_tracecat_image_tag=)${VERSION_SEARCH_PATTERN}/\\1${escaped_new_version}/g" "$file" && \
    echo "✓ Updated $file" || echo "✗ Failed to update $file"
}

# Main execution
echo "Updating release tag from $CURRENT_VERSION to $NEW_VERSION"
echo "Updating Python package version from $CURRENT_PYTHON_VERSION to $NEW_PYTHON_VERSION"
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
    if [ "$file" = "$INIT_FILE" ] || [ "$file" = "$REGISTRY_INIT_FILE" ]; then
        update_python_version_file "$file"
    else
        update_release_tag_file "$file"
    fi
done

echo "----------------------------------------"
echo -e "\033[32mVersion update complete!\033[0m"
echo -e "\033[33mPlease review the changes before committing.\033[0m"

# Copy new version to clipboard
if command -v pbcopy &> /dev/null; then
    echo -n "$NEW_VERSION" | pbcopy
    echo -e "\033[36mNew release tag ($NEW_VERSION) copied to clipboard!\033[0m"
elif command -v xclip &> /dev/null; then
    echo -n "$NEW_VERSION" | xclip -selection clipboard
    echo -e "\033[36mNew release tag ($NEW_VERSION) copied to clipboard!\033[0m"
elif command -v wl-copy &> /dev/null; then
    echo -n "$NEW_VERSION" | wl-copy
    echo -e "\033[36mNew release tag ($NEW_VERSION) copied to clipboard!\033[0m"
else
    echo -e "\033[33mClipboard functionality not available (pbcopy/xclip/wl-copy not found)\033[0m"
fi
