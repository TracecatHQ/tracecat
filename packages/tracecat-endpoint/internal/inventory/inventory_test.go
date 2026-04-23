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
	projectSettingsPath := filepath.Join(homeDir, "workspace-alpha", ".claude", "settings.json")
	claudePath := filepath.Join(homeDir, "workspace-alpha", "CLAUDE.md")
	claudeLocalPath := filepath.Join(homeDir, "workspace-alpha", "CLAUDE.local.md")
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
	projectPermissionAsset := findAsset(t, assets, assetClassPermissions, assetTypePermissionConfig, projectSettingsPath+"#permission_config")
	if projectPermissionAsset.Metadata["source_surface"] != "project_settings_json" {
		t.Fatalf("unexpected project permission source %v", projectPermissionAsset.Metadata["source_surface"])
	}
	projectSandboxAsset := findAsset(t, assets, assetClassSandbox, assetTypeSandboxConfig, projectSettingsPath+"#sandbox_config")
	if projectSandboxAsset.Metadata["writable"] != false {
		t.Fatalf("expected project settings sandbox surface to be non-writable, got %v", projectSandboxAsset.Metadata["writable"])
	}

	httpMCP := findAssetByDisplayName(t, assets, assetClassMCPServer, assetTypeMCPServer, "github-http")
	if httpMCP.Metadata["resolved_identity"] != "https://api.github.com/mcp" {
		t.Fatalf("unexpected http mcp identity %v", httpMCP.Metadata["resolved_identity"])
	}
	approvalIdentity, ok := httpMCP.Metadata["approval_identity"].(map[string]any)
	if !ok {
		t.Fatalf("expected approval_identity metadata, got %T", httpMCP.Metadata["approval_identity"])
	}
	if approvalIdentity["server_name"] != "github-http" || approvalIdentity["resolved_identity"] != "https://api.github.com/mcp" {
		t.Fatalf("unexpected approval identity %v", approvalIdentity)
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
	urls, ok := claudeFile.Evidence["urls"].([]string)
	if !ok || len(urls) != 1 || urls[0] != "https://example.com" {
		t.Fatalf("unexpected claude file urls %v", claudeFile.Evidence["urls"])
	}
	ips, ok := claudeFile.Evidence["ips"].([]string)
	if !ok || len(ips) != 1 || ips[0] != "10.0.0.8" {
		t.Fatalf("unexpected claude file ips %v", claudeFile.Evidence["ips"])
	}

	claudeLocalFile := findAsset(t, assets, assetClassInstructionFile, assetTypeClaudeMD, claudeLocalPath)
	languageSignal, ok := claudeLocalFile.Evidence["language_signal"].(map[string]any)
	if !ok || languageSignal["likely_english"] != false {
		t.Fatalf("unexpected claude.local language signal %v", claudeLocalFile.Evidence["language_signal"])
	}
	obfuscation, ok := claudeLocalFile.Evidence["obfuscation"].(map[string]any)
	if !ok || obfuscation["obfuscation_detected"] != true {
		t.Fatalf("unexpected claude.local obfuscation signal %v", claudeLocalFile.Evidence["obfuscation"])
	}
	domains, ok := claudeLocalFile.Evidence["domains"].([]string)
	if !ok || len(domains) != 1 || domains[0] != "control.example.net" {
		t.Fatalf("unexpected claude.local domains %v", claudeLocalFile.Evidence["domains"])
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

func TestClaudeProviderEmitsParseErrorAssetsFromFixtureSurfaces(t *testing.T) {
	t.Parallel()

	homeDir := copyFixture(t, "claude-invalid-surfaces")
	provider := NewClaudeProvider(homeDir)

	assets, err := provider.Collect(context.Background())
	if err != nil {
		t.Fatalf("Collect() error = %v", err)
	}

	testCases := []struct {
		assetClass string
		assetType  string
		identity   string
	}{
		{
			assetClass: assetClassMCPServer,
			assetType:  assetTypeMCPServer,
			identity:   filepath.Join(homeDir, ".claude", "settings.json") + "#parse_error#mcp_server",
		},
		{
			assetClass: assetClassPermissions,
			assetType:  assetTypePermissionConfig,
			identity:   filepath.Join(homeDir, "workspace-alpha", ".claude", "settings.json") + "#parse_error#permission_config",
		},
		{
			assetClass: assetClassSandbox,
			assetType:  assetTypeSandboxConfig,
			identity:   filepath.Join(homeDir, "workspace-alpha", ".claude", "settings.local.json") + "#parse_error#sandbox_config",
		},
		{
			assetClass: assetClassMCPServer,
			assetType:  assetTypeMCPServer,
			identity:   filepath.Join(homeDir, "workspace-alpha", ".mcp.json") + "#parse_error#mcp_server",
		},
	}

	for _, tc := range testCases {
		asset := findAsset(t, assets, tc.assetClass, tc.assetType, tc.identity)
		if asset.Metadata["parse_status"] != parseStatusInvalid {
			t.Fatalf("unexpected parse status for %s: %v", tc.identity, asset.Metadata["parse_status"])
		}
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
