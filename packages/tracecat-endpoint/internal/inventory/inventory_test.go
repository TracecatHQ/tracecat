package inventory

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/claude"
	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/spmapi"
)

func TestClaudeProviderCollectsClaudeSurfaces(t *testing.T) {
	t.Parallel()

	homeDir := copyFixture(t, "claude")
	provider := NewClaudeProvider(homeDir)

	assets, err := provider.Collect(context.Background())
	if err != nil {
		t.Fatalf("Collect() error = %v", err)
	}
	if len(assets) == 0 {
		t.Fatal("expected assets to be collected")
	}

	trustedPath := filepath.Join(homeDir, "workspace-alpha")
	additionalPath := filepath.Join(homeDir, "workspace-beta")
	claudePath := filepath.Join(homeDir, "workspace-alpha", "CLAUDE.md")
	agentsPath := filepath.Join(homeDir, "workspace-alpha", "AGENTS.md")

	trusted := findAsset(t, assets, assetClassWorkspaceAccess, assetTypeTrustedDirectory, trustedPath)
	if trusted.Metadata["source_surface"] != "user_state_json" {
		t.Fatalf("unexpected trusted directory source %v", trusted.Metadata["source_surface"])
	}

	additional := findAsset(t, assets, assetClassWorkspaceAccess, assetTypeAdditionalDirectory, additionalPath)
	if additional.IdentityKey != additionalPath {
		t.Fatalf("unexpected additional directory identity %q", additional.IdentityKey)
	}

	permissionAsset := findAssetBySuffix(t, assets, assetClassPermissions, assetTypePermissionConfig, ".claude/settings.json#permission_config")
	if permissionAsset.Metadata["parse_status"] != parseStatusOK {
		t.Fatalf("unexpected permission parse status %v", permissionAsset.Metadata["parse_status"])
	}

	httpMCP := findAssetByDisplayName(t, assets, assetClassMCPServer, assetTypeMCPServer, "github-http")
	if httpMCP.Metadata["resolved_identity"] != "https://api.github.com/mcp" {
		t.Fatalf("unexpected http mcp identity %v", httpMCP.Metadata["resolved_identity"])
	}

	stdioMCP := findAssetByDisplayName(t, assets, assetClassMCPServer, assetTypeMCPServer, "local-stdio")
	if stdioMCP.Metadata["resolved_identity"] != "package:@tracecat/mcp" {
		t.Fatalf("unexpected stdio mcp identity %v", stdioMCP.Metadata["resolved_identity"])
	}

	projectMCP := findAssetByDisplayName(t, assets, assetClassMCPServer, assetTypeMCPServer, "slack")
	if projectMCP.Metadata["resolved_identity"] != "package:slack-mcp" {
		t.Fatalf("unexpected project mcp identity %v", projectMCP.Metadata["resolved_identity"])
	}

	projectLocalMCP := findAssetByDisplayName(t, assets, assetClassMCPServer, assetTypeMCPServer, "github-project")
	if projectLocalMCP.Metadata["resolved_identity"] != "https://api.github.com/mcp" {
		t.Fatalf("unexpected project-local mcp identity %v", projectLocalMCP.Metadata["resolved_identity"])
	}

	claudeFile := findAsset(t, assets, assetClassInstructionFile, assetTypeClaudeMD, claudePath)
	if claudeFile.Evidence["urls"] == nil {
		t.Fatal("expected claude file evidence urls")
	}

	agentsFile := findAsset(t, assets, assetClassInstructionFile, assetTypeAgentsMD, agentsPath)
	if agentsFile.Metadata["enforceable"] != false {
		t.Fatalf("expected AGENTS.md to be inventory-only, got %v", agentsFile.Metadata["enforceable"])
	}

	findAssetByDisplayName(t, assets, assetClassExtension, assetTypeHook, "PreToolUse .* echo audit")
	findAssetByDisplayName(t, assets, assetClassSkill, assetTypeSkill, "registry-review")
	findAssetByDisplayName(t, assets, assetClassAgent, assetTypeSubagent, "investigator")
}

func TestClaudeProviderEmitsParseErrorAssetsWithoutFailingSync(t *testing.T) {
	t.Parallel()

	homeDir := copyFixture(t, "claude-invalid")
	provider := NewClaudeProvider(homeDir)

	assets, err := provider.Collect(context.Background())
	if err != nil {
		t.Fatalf("Collect() error = %v", err)
	}
	if len(assets) == 0 {
		t.Fatal("expected parse-error assets to be returned")
	}

	parseErrorAsset := findAssetBySuffix(t, assets, assetClassMCPServer, assetTypeMCPServer, ".claude.json#parse_error#mcp_server")
	if parseErrorAsset.Metadata["parse_status"] != parseStatusInvalid {
		t.Fatalf("unexpected parse status %v", parseErrorAsset.Metadata["parse_status"])
	}
}

func TestClaudeProviderDoesNotCrawlUndiscoveredProjectRoots(t *testing.T) {
	t.Parallel()

	homeDir := copyFixture(t, "claude")
	undiscoveredRoot := filepath.Join(homeDir, "workspace-hidden")
	if err := os.MkdirAll(filepath.Join(undiscoveredRoot, ".claude"), 0o755); err != nil {
		t.Fatalf("mkdir hidden root: %v", err)
	}
	if err := os.WriteFile(filepath.Join(undiscoveredRoot, "CLAUDE.md"), []byte("hidden instructions"), 0o600); err != nil {
		t.Fatalf("write hidden CLAUDE.md: %v", err)
	}
	if err := os.WriteFile(filepath.Join(undiscoveredRoot, ".mcp.json"), []byte(`{"mcpServers":{"hidden":{"url":"https://hidden.example/mcp"}}}`), 0o600); err != nil {
		t.Fatalf("write hidden .mcp.json: %v", err)
	}

	provider := NewClaudeProvider(homeDir)
	assets, err := provider.Collect(context.Background())
	if err != nil {
		t.Fatalf("Collect() error = %v", err)
	}

	assertAssetMissing(t, assets, assetClassInstructionFile, assetTypeClaudeMD, filepath.Join(undiscoveredRoot, "CLAUDE.md"))
	assertDisplayNameMissing(t, assets, assetClassMCPServer, assetTypeMCPServer, "hidden")
}

func TestClaudeProviderEmitsMCPParseErrorsForSettingsSurfaces(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name         string
		relativePath string
	}{
		{name: "user settings", relativePath: filepath.Join(".claude", "settings.json")},
		{name: "project settings", relativePath: filepath.Join("workspace-alpha", ".claude", "settings.json")},
		{name: "project local settings", relativePath: filepath.Join("workspace-alpha", ".claude", "settings.local.json")},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			homeDir := copyFixture(t, "claude")
			targetPath := filepath.Join(homeDir, tc.relativePath)
			if err := os.MkdirAll(filepath.Dir(targetPath), 0o755); err != nil {
				t.Fatalf("mkdir %s: %v", tc.relativePath, err)
			}
			if err := os.WriteFile(targetPath, []byte(`{"mcpServers":{"broken":`), 0o600); err != nil {
				t.Fatalf("write %s: %v", tc.relativePath, err)
			}

			provider := NewClaudeProvider(homeDir)
			assets, err := provider.Collect(context.Background())
			if err != nil {
				t.Fatalf("Collect() error = %v", err)
			}

			parseErrorAsset := findAssetBySuffix(t, assets, assetClassMCPServer, assetTypeMCPServer, tc.relativePath+"#parse_error#mcp_server")
			if parseErrorAsset.Metadata["parse_status"] != parseStatusInvalid {
				t.Fatalf("unexpected parse status %v", parseErrorAsset.Metadata["parse_status"])
			}
		})
	}
}

func TestResolveMCPIdentityUsesDeterministicTransportSpecificKeys(t *testing.T) {
	t.Parallel()

	httpIdentity := claude.ResolveMCPIdentity(map[string]any{
		"url": "HTTPS://api.github.com/mcp/",
	})
	if httpIdentity.Transport != "http" {
		t.Fatalf("unexpected transport %q", httpIdentity.Transport)
	}
	if httpIdentity.Resolved != "https://api.github.com/mcp" {
		t.Fatalf("unexpected http identity %q", httpIdentity.Resolved)
	}

	stdioIdentity := claude.ResolveMCPIdentity(map[string]any{
		"command": "uvx",
		"args":    []any{"slack-mcp", "--stdio"},
	})
	if stdioIdentity.Transport != "stdio" {
		t.Fatalf("unexpected transport %q", stdioIdentity.Transport)
	}
	if stdioIdentity.Resolved != "package:slack-mcp" {
		t.Fatalf("unexpected stdio identity %q", stdioIdentity.Resolved)
	}
}

func copyFixture(t *testing.T, fixtureName string) string {
	t.Helper()

	root := filepath.Join("testdata", fixtureName, "home")
	targetHome := filepath.Join(t.TempDir(), "home")
	if err := copyDirectory(root, targetHome, targetHome); err != nil {
		t.Fatalf("copy fixture %s: %v", fixtureName, err)
	}
	return targetHome
}

func copyDirectory(source string, target string, homeDir string) error {
	entries, err := os.ReadDir(source)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(target, 0o755); err != nil {
		return err
	}
	for _, entry := range entries {
		sourcePath := filepath.Join(source, entry.Name())
		targetPath := filepath.Join(target, entry.Name())
		if entry.IsDir() {
			if err := copyDirectory(sourcePath, targetPath, homeDir); err != nil {
				return err
			}
			continue
		}
		data, err := os.ReadFile(sourcePath)
		if err != nil {
			return err
		}
		replaced := strings.ReplaceAll(string(data), "__HOME__", homeDir)
		if err := os.MkdirAll(filepath.Dir(targetPath), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(targetPath, []byte(replaced), 0o600); err != nil {
			return err
		}
	}
	return nil
}

func findAsset(
	t *testing.T,
	assets []spmapi.SyncAsset,
	assetClass string,
	assetType string,
	identity string,
) spmapi.SyncAsset {
	t.Helper()
	for _, asset := range assets {
		if asset.AssetClass == assetClass && asset.AssetType == assetType && asset.IdentityKey == identity {
			return asset
		}
	}
	t.Fatalf("asset %s/%s with identity %s not found", assetClass, assetType, identity)
	return spmapi.SyncAsset{}
}

func findAssetByDisplayName(
	t *testing.T,
	assets []spmapi.SyncAsset,
	assetClass string,
	assetType string,
	displayName string,
) spmapi.SyncAsset {
	t.Helper()
	for _, asset := range assets {
		if asset.AssetClass == assetClass && asset.AssetType == assetType && asset.DisplayName == displayName {
			return asset
		}
	}
	t.Fatalf("asset %s/%s with display name %s not found", assetClass, assetType, displayName)
	return spmapi.SyncAsset{}
}

func findAssetBySuffix(
	t *testing.T,
	assets []spmapi.SyncAsset,
	assetClass string,
	assetType string,
	suffix string,
) spmapi.SyncAsset {
	t.Helper()
	for _, asset := range assets {
		if asset.AssetClass == assetClass && asset.AssetType == assetType && strings.HasSuffix(asset.IdentityKey, suffix) {
			return asset
		}
	}
	t.Fatalf("asset %s/%s with identity suffix %s not found", assetClass, assetType, suffix)
	return spmapi.SyncAsset{}
}

func assertAssetMissing(
	t *testing.T,
	assets []spmapi.SyncAsset,
	assetClass string,
	assetType string,
	identity string,
) {
	t.Helper()
	for _, asset := range assets {
		if asset.AssetClass == assetClass && asset.AssetType == assetType && asset.IdentityKey == identity {
			t.Fatalf("unexpected asset %s/%s with identity %s", assetClass, assetType, identity)
		}
	}
}

func assertDisplayNameMissing(
	t *testing.T,
	assets []spmapi.SyncAsset,
	assetClass string,
	assetType string,
	displayName string,
) {
	t.Helper()
	for _, asset := range assets {
		if asset.AssetClass == assetClass && asset.AssetType == assetType && asset.DisplayName == displayName {
			t.Fatalf("unexpected asset %s/%s with display name %s", assetClass, assetType, displayName)
		}
	}
}
