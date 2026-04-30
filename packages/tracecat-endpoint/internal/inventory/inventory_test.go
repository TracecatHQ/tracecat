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

	snapshot, err := provider.Collect(context.Background())
	if err != nil {
		t.Fatalf("Collect() error = %v", err)
	}
	items := snapshot.InventoryItems
	if len(items) == 0 {
		t.Fatal("expected items to be collected")
	}

	trustedPath := filepath.Join(homeDir, "workspace-alpha")
	additionalPath := filepath.Join(homeDir, "workspace-beta")
	projectSettingsPath := filepath.Join(homeDir, "workspace-alpha", ".claude", "settings.json")
	claudePath := filepath.Join(homeDir, "workspace-alpha", "CLAUDE.md")
	claudeLocalPath := filepath.Join(homeDir, "workspace-alpha", "CLAUDE.local.md")
	agentsPath := filepath.Join(homeDir, "workspace-alpha", "AGENTS.md")

	trusted := findItem(t, items, itemTypeTrustedDirectory, sourceTypeDirectory, trustedPath)
	if trusted.Metadata["source_surface"] != "user_state_json" {
		t.Fatalf("unexpected trusted directory source %v", trusted.Metadata["source_surface"])
	}

	additional := findItem(t, items, itemTypeAdditionalDirectory, sourceTypeDirectory, additionalPath)
	if additional.IdentityKey != additionalPath {
		t.Fatalf("unexpected additional directory identity %q", additional.IdentityKey)
	}

	permissionItem := findItemBySuffix(t, items, itemTypePermissionConfig, sourceTypeSettingsJSON, ".claude/settings.json#permission_config")
	if permissionItem.Metadata["parse_status"] != parseStatusOK {
		t.Fatalf("unexpected permission parse status %v", permissionItem.Metadata["parse_status"])
	}
	projectPermissionItem := findItem(t, items, itemTypePermissionConfig, sourceTypeSettingsJSON, projectSettingsPath+"#permission_config")
	if projectPermissionItem.Metadata["source_surface"] != "project_settings_json" {
		t.Fatalf("unexpected project permission source %v", projectPermissionItem.Metadata["source_surface"])
	}
	projectSandboxItem := findItem(t, items, itemTypeSandboxConfig, sourceTypeSettingsJSON, projectSettingsPath+"#sandbox_config")
	if projectSandboxItem.Metadata["writable"] != false {
		t.Fatalf("expected project settings sandbox surface to be non-writable, got %v", projectSandboxItem.Metadata["writable"])
	}

	httpMCP := findItemByDisplayName(t, items, itemTypeMCPServer, "github-http")
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

	stdioMCP := findItemByDisplayName(t, items, itemTypeMCPServer, "local-stdio")
	if stdioMCP.Metadata["resolved_identity"] != "package:@tracecat/mcp" {
		t.Fatalf("unexpected stdio mcp identity %v", stdioMCP.Metadata["resolved_identity"])
	}

	projectMCP := findItemByDisplayName(t, items, itemTypeMCPServer, "slack")
	if projectMCP.Metadata["resolved_identity"] != "package:slack-mcp" {
		t.Fatalf("unexpected project mcp identity %v", projectMCP.Metadata["resolved_identity"])
	}

	projectLocalMCP := findItemByDisplayName(t, items, itemTypeMCPServer, "github-project")
	if projectLocalMCP.Metadata["resolved_identity"] != "https://api.github.com/mcp" {
		t.Fatalf("unexpected project-local mcp identity %v", projectLocalMCP.Metadata["resolved_identity"])
	}

	claudeFile := findItem(t, items, itemTypeInstructionFile, sourceTypeClaudeMD, claudePath)
	urls, ok := claudeFile.Evidence["urls"].([]string)
	if !ok || len(urls) != 1 || urls[0] != "https://example.com" {
		t.Fatalf("unexpected claude file urls %v", claudeFile.Evidence["urls"])
	}
	ips, ok := claudeFile.Evidence["ips"].([]string)
	if !ok || len(ips) != 1 || ips[0] != "10.0.0.8" {
		t.Fatalf("unexpected claude file ips %v", claudeFile.Evidence["ips"])
	}

	claudeLocalFile := findItem(t, items, itemTypeInstructionFile, sourceTypeClaudeLocalMD, claudeLocalPath)
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

	agentsFile := findItem(t, items, itemTypeInstructionFile, sourceTypeAgentsMD, agentsPath)
	if agentsFile.Metadata["enforceable"] != false {
		t.Fatalf("expected AGENTS.md to be inventory-only, got %v", agentsFile.Metadata["enforceable"])
	}

	findItemByDisplayName(t, items, itemTypeHook, "PreToolUse .* echo audit")
	findItemByDisplayName(t, items, itemTypeSkill, "registry-review")
	findItemByDisplayName(t, items, itemTypeSubagent, "investigator")
}

func TestClaudeProviderCollectsPluginBOM(t *testing.T) {
	t.Parallel()

	homeDir := copyFixture(t, "claude")
	pluginRoot := filepath.Join(homeDir, ".claude", "plugins", "demo")
	binaryPath := filepath.Join(pluginRoot, "bin", "helper")
	if err := os.Chmod(binaryPath, 0o755); err != nil {
		t.Fatalf("chmod helper binary: %v", err)
	}

	provider := NewClaudeProvider(homeDir)
	snapshot, err := provider.Collect(context.Background())
	if err != nil {
		t.Fatalf("Collect() error = %v", err)
	}
	items := snapshot.InventoryItems
	pluginManifestPath := filepath.Join(pluginRoot, ".claude-plugin", "plugin.json")
	plugin := findItem(t, items, itemTypePlugin, sourceTypePluginManifest, pluginManifestPath)
	if plugin.ItemLocation != pluginRoot {
		t.Fatalf("expected plugin item location %q, got %q", pluginRoot, plugin.ItemLocation)
	}

	expectedChildren := []spmapi.SyncInventoryItem{
		findItemByDisplayName(t, items, itemTypeSkill, "review"),
		findItemByDisplayName(t, items, itemTypeSubagent, "plugin-investigator"),
		findItemByDisplayName(t, items, itemTypeCommand, "deploy"),
		findItemByDisplayName(t, items, itemTypeHook, "PostToolUse Write demo audit"),
		findItemByDisplayName(t, items, itemTypeMCPServer, "demo-mcp"),
		findItemByDisplayName(t, items, itemTypeLSPServer, "demo-lsp"),
		findItemByDisplayName(t, items, itemTypeMonitor, "demo-monitor"),
		findItemByDisplayName(t, items, itemTypeBinary, "helper"),
		findItemByDisplayName(t, items, itemTypePluginSettings, "settings.json"),
		findItemByDisplayName(t, items, itemTypeOutputStyle, "terse-review"),
		findItemByDisplayName(t, items, itemTypeTheme, "dark"),
	}

	childIdentities := map[string]string{}
	for _, child := range expectedChildren {
		childIdentities[child.IdentityKey] = child.ItemType
	}
	seenChildren := map[string]struct{}{}
	for _, relationship := range snapshot.Relationships {
		if relationship.RelationshipType != relationshipTypeDefines {
			t.Fatalf("expected defines relationship, got %q", relationship.RelationshipType)
		}
		if relationship.FromIdentityKey != plugin.IdentityKey {
			continue
		}
		itemType, ok := childIdentities[relationship.ToIdentityKey]
		if !ok {
			t.Fatalf("unexpected plugin child relationship to %q", relationship.ToIdentityKey)
		}
		if itemType == itemTypeTrustedDirectory || itemType == itemTypeAdditionalDirectory {
			t.Fatalf("plugin child relationship points to directory item %q", relationship.ToIdentityKey)
		}
		seenChildren[relationship.ToIdentityKey] = struct{}{}
	}
	if len(seenChildren) != len(expectedChildren) {
		t.Fatalf("expected %d plugin child relationships, got %d", len(expectedChildren), len(seenChildren))
	}
}

func TestClaudeProviderEmitsPluginParseErrorItems(t *testing.T) {
	t.Parallel()

	homeDir := copyFixture(t, "claude")
	pluginRoot := filepath.Join(homeDir, ".claude", "plugins", "demo")
	invalidPaths := []string{
		filepath.Join(pluginRoot, ".claude-plugin", "plugin.json"),
		filepath.Join(pluginRoot, ".lsp.json"),
		filepath.Join(pluginRoot, "monitors", "monitors.json"),
		filepath.Join(pluginRoot, "settings.json"),
	}
	for _, path := range invalidPaths {
		if err := os.WriteFile(path, []byte(`{"invalid":`), 0o600); err != nil {
			t.Fatalf("write invalid fixture %s: %v", path, err)
		}
	}

	provider := NewClaudeProvider(homeDir)
	snapshot, err := provider.Collect(context.Background())
	if err != nil {
		t.Fatalf("Collect() error = %v", err)
	}
	items := snapshot.InventoryItems

	plugin := findItemBySuffix(t, items, itemTypePlugin, sourceTypePluginManifest, filepath.Join(".claude-plugin", "plugin.json"))
	if plugin.Metadata["parse_status"] != parseStatusInvalid {
		t.Fatalf("unexpected plugin parse status %v", plugin.Metadata["parse_status"])
	}
	lsp := findItemBySuffix(t, items, itemTypeLSPServer, sourceTypeLSPJSON, ".lsp.json#parse_error#lsp_server")
	if lsp.Metadata["parse_status"] != parseStatusInvalid {
		t.Fatalf("unexpected lsp parse status %v", lsp.Metadata["parse_status"])
	}
	monitor := findItemBySuffix(t, items, itemTypeMonitor, sourceTypeMonitorsJSON, filepath.Join("monitors", "monitors.json")+"#parse_error#monitor")
	if monitor.Metadata["parse_status"] != parseStatusInvalid {
		t.Fatalf("unexpected monitor parse status %v", monitor.Metadata["parse_status"])
	}
	settings := findItemBySuffix(t, items, itemTypePluginSettings, sourceTypePluginSettingsJSON, "settings.json#plugin_settings")
	if settings.Metadata["parse_status"] != parseStatusInvalid {
		t.Fatalf("unexpected settings parse status %v", settings.Metadata["parse_status"])
	}
	for _, child := range []spmapi.SyncInventoryItem{lsp, monitor, settings} {
		assertRelationshipExists(t, snapshot.Relationships, plugin.IdentityKey, child.IdentityKey)
	}
}

func TestInventoryCollectorRejectsLegacyRelationshipTypes(t *testing.T) {
	t.Parallel()

	collector := newInventoryCollector()
	collector.addRelationship(spmapi.SyncInventoryRelationship{
		RelationshipType: "contains",
		FromIdentityKey:  "plugin",
		ToIdentityKey:    "skill",
	})

	if _, err := collector.snapshot(); err == nil {
		t.Fatal("expected legacy relationship type to be rejected")
	}
}

func TestClaudeProviderEmitsParseErrorItemsWithoutFailingSync(t *testing.T) {
	t.Parallel()

	homeDir := copyFixture(t, "claude-invalid")
	provider := NewClaudeProvider(homeDir)

	snapshot, err := provider.Collect(context.Background())
	if err != nil {
		t.Fatalf("Collect() error = %v", err)
	}
	items := snapshot.InventoryItems
	if len(items) == 0 {
		t.Fatal("expected parse-error items to be returned")
	}

	parseErrorItem := findItemBySuffix(t, items, itemTypeMCPServer, sourceTypeClaudeJSON, ".claude.json#parse_error#mcp_server")
	if parseErrorItem.Metadata["parse_status"] != parseStatusInvalid {
		t.Fatalf("unexpected parse status %v", parseErrorItem.Metadata["parse_status"])
	}
}

func TestClaudeProviderEmitsParseErrorItemsFromFixtureSurfaces(t *testing.T) {
	t.Parallel()

	homeDir := copyFixture(t, "claude-invalid-surfaces")
	provider := NewClaudeProvider(homeDir)

	snapshot, err := provider.Collect(context.Background())
	if err != nil {
		t.Fatalf("Collect() error = %v", err)
	}
	items := snapshot.InventoryItems

	testCases := []struct {
		itemType   string
		sourceType string
		identity   string
	}{
		{
			itemType:   itemTypeMCPServer,
			sourceType: sourceTypeSettingsJSON,
			identity:   filepath.Join(homeDir, ".claude", "settings.json") + "#parse_error#mcp_server",
		},
		{
			itemType:   itemTypePermissionConfig,
			sourceType: sourceTypeSettingsJSON,
			identity:   filepath.Join(homeDir, "workspace-alpha", ".claude", "settings.json") + "#parse_error#permission_config",
		},
		{
			itemType:   itemTypeSandboxConfig,
			sourceType: sourceTypeSettingsLocalJSON,
			identity:   filepath.Join(homeDir, "workspace-alpha", ".claude", "settings.local.json") + "#parse_error#sandbox_config",
		},
		{
			itemType:   itemTypeMCPServer,
			sourceType: sourceTypeMCPJSON,
			identity:   filepath.Join(homeDir, "workspace-alpha", ".mcp.json") + "#parse_error#mcp_server",
		},
	}

	for _, tc := range testCases {
		item := findItem(t, items, tc.itemType, tc.sourceType, tc.identity)
		if item.Metadata["parse_status"] != parseStatusInvalid {
			t.Fatalf("unexpected parse status for %s: %v", tc.identity, item.Metadata["parse_status"])
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
	snapshot, err := provider.Collect(context.Background())
	if err != nil {
		t.Fatalf("Collect() error = %v", err)
	}
	items := snapshot.InventoryItems

	assertItemMissing(t, items, itemTypeInstructionFile, sourceTypeClaudeMD, filepath.Join(undiscoveredRoot, "CLAUDE.md"))
	assertDisplayNameMissing(t, items, itemTypeMCPServer, "hidden")
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
			snapshot, err := provider.Collect(context.Background())
			if err != nil {
				t.Fatalf("Collect() error = %v", err)
			}
			items := snapshot.InventoryItems

			parseErrorItem := findItemBySuffix(t, items, itemTypeMCPServer, sourceTypeFromPath(tc.relativePath), tc.relativePath+"#parse_error#mcp_server")
			if parseErrorItem.Metadata["parse_status"] != parseStatusInvalid {
				t.Fatalf("unexpected parse status %v", parseErrorItem.Metadata["parse_status"])
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

func findItem(
	t *testing.T,
	items []spmapi.SyncInventoryItem,
	itemType string,
	sourceType string,
	identity string,
) spmapi.SyncInventoryItem {
	t.Helper()
	for _, item := range items {
		if item.ItemType == itemType && item.SourceType == sourceType && item.IdentityKey == identity {
			return item
		}
	}
	t.Fatalf("item %s/%s with identity %s not found", itemType, sourceType, identity)
	return spmapi.SyncInventoryItem{}
}

func findItemByDisplayName(
	t *testing.T,
	items []spmapi.SyncInventoryItem,
	itemType string,
	displayName string,
) spmapi.SyncInventoryItem {
	t.Helper()
	for _, item := range items {
		if item.ItemType == itemType && item.DisplayName == displayName {
			return item
		}
	}
	t.Fatalf("item %s with display name %s not found", itemType, displayName)
	return spmapi.SyncInventoryItem{}
}

func findItemBySuffix(
	t *testing.T,
	items []spmapi.SyncInventoryItem,
	itemType string,
	sourceType string,
	suffix string,
) spmapi.SyncInventoryItem {
	t.Helper()
	for _, item := range items {
		if item.ItemType == itemType && item.SourceType == sourceType && strings.HasSuffix(item.IdentityKey, suffix) {
			return item
		}
	}
	t.Fatalf("item %s/%s with identity suffix %s not found", itemType, sourceType, suffix)
	return spmapi.SyncInventoryItem{}
}

func assertItemMissing(
	t *testing.T,
	items []spmapi.SyncInventoryItem,
	itemType string,
	sourceType string,
	identity string,
) {
	t.Helper()
	for _, item := range items {
		if item.ItemType == itemType && item.SourceType == sourceType && item.IdentityKey == identity {
			t.Fatalf("unexpected item %s/%s with identity %s", itemType, sourceType, identity)
		}
	}
}

func assertDisplayNameMissing(
	t *testing.T,
	items []spmapi.SyncInventoryItem,
	itemType string,
	displayName string,
) {
	t.Helper()
	for _, item := range items {
		if item.ItemType == itemType && item.DisplayName == displayName {
			t.Fatalf("unexpected item %s with display name %s", itemType, displayName)
		}
	}
}

func assertRelationshipExists(
	t *testing.T,
	relationships []spmapi.SyncInventoryRelationship,
	fromIdentity string,
	toIdentity string,
) {
	t.Helper()
	for _, relationship := range relationships {
		if relationship.RelationshipType == relationshipTypeDefines &&
			relationship.FromIdentityKey == fromIdentity &&
			relationship.ToIdentityKey == toIdentity {
			return
		}
	}
	t.Fatalf("defines relationship %s -> %s not found", fromIdentity, toIdentity)
}

func sourceTypeFromPath(path string) string {
	switch filepath.Base(path) {
	case "settings.local.json":
		return sourceTypeSettingsLocalJSON
	case "settings.json":
		return sourceTypeSettingsJSON
	case ".mcp.json":
		return sourceTypeMCPJSON
	default:
		return sourceTypeClaudeJSON
	}
}
