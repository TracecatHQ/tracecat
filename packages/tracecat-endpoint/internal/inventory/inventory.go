package inventory

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/spmapi"
)

const (
	claudeHarness = "claude_code"

	assetClassWorkspaceAccess = "workspace_access"
	assetClassPermissions     = "permissions"
	assetClassSandbox         = "sandbox"
	assetClassMCPServer       = "mcp_server"
	assetClassSkill           = "skill"
	assetClassExtension       = "extension"
	assetClassInstructionFile = "instruction_file"
	assetClassAgent           = "agent"

	assetTypeTrustedDirectory    = "trusted_directory"
	assetTypeAdditionalDirectory = "additional_directory"
	assetTypePermissionConfig    = "permission_config"
	assetTypeSandboxConfig       = "sandbox_config"
	assetTypeMCPServer           = "mcp_server"
	assetTypeSkill               = "skill"
	assetTypeHook                = "hook"
	assetTypeClaudeMD            = "claude_md"
	assetTypeAgentsMD            = "agents_md"
	assetTypeSubagent            = "subagent"

	parseStatusOK         = "ok"
	parseStatusInvalid    = "invalid"
	parseStatusUnreadable = "unreadable"
)

var (
	urlPattern             = regexp.MustCompile(`https?://[^\s<>"')\]]+`)
	domainPattern          = regexp.MustCompile(`(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\b`)
	ipv4Pattern            = regexp.MustCompile(`\b(?:\d{1,3}\.){3}\d{1,3}\b`)
	ipv6Pattern            = regexp.MustCompile(`(?i)\b(?:[a-f0-9]{1,4}:){2,}[a-f0-9]{1,4}\b`)
	base64CandidatePattern = regexp.MustCompile(`\b[A-Za-z0-9+/]{24,}={0,2}\b`)
	tokenPattern           = regexp.MustCompile(`\b[A-Za-z0-9+/=_-]{20,}\b`)
)

// Provider returns the current local inventory for sync requests.
type Provider interface {
	Collect(context.Context) ([]spmapi.SyncAsset, error)
}

// ClaudeProvider inventories Claude Code user and project surfaces.
type ClaudeProvider struct {
	HomeDir string
}

func NewClaudeProvider(homeDir string) ClaudeProvider {
	return ClaudeProvider{HomeDir: strings.TrimSpace(homeDir)}
}

func (p ClaudeProvider) Collect(ctx context.Context) ([]spmapi.SyncAsset, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if p.HomeDir == "" {
		return nil, fmt.Errorf("home directory is required")
	}

	collector := newAssetCollector()
	projectRoots := map[string]projectDirectory{}

	userSettingsPath := filepath.Join(p.HomeDir, ".claude", "settings.json")
	if err := collectJSONSurface(collector, jsonSurfaceConfig{
		Path:          userSettingsPath,
		SourceSurface: "user_settings_json",
		ProjectRoot:   "",
		Writable:      true,
		EmitParseOnError: []parseErrorSurface{
			{AssetClass: assetClassPermissions, AssetType: assetTypePermissionConfig, DisplaySuffix: "permissions"},
			{AssetClass: assetClassSandbox, AssetType: assetTypeSandboxConfig, DisplaySuffix: "sandbox"},
		},
		DiscoverProjects: true,
	}, projectRoots); err != nil {
		return nil, err
	}

	userStatePath := filepath.Join(p.HomeDir, ".claude.json")
	if err := collectJSONSurface(collector, jsonSurfaceConfig{
		Path:          userStatePath,
		SourceSurface: "user_state_json",
		ProjectRoot:   "",
		Writable:      true,
		EmitParseOnError: []parseErrorSurface{
			{AssetClass: assetClassPermissions, AssetType: assetTypePermissionConfig, DisplaySuffix: "permissions"},
			{AssetClass: assetClassSandbox, AssetType: assetTypeSandboxConfig, DisplaySuffix: "sandbox"},
			{AssetClass: assetClassMCPServer, AssetType: assetTypeMCPServer, DisplaySuffix: "mcp"},
		},
		DiscoverProjects: true,
	}, projectRoots); err != nil {
		return nil, err
	}

	roots := make([]projectDirectory, 0, len(projectRoots))
	for _, root := range projectRoots {
		roots = append(roots, root)
	}
	sort.Slice(roots, func(i, j int) bool {
		if roots[i].AssetType == roots[j].AssetType {
			return roots[i].Path < roots[j].Path
		}
		return roots[i].AssetType < roots[j].AssetType
	})

	for _, root := range roots {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		collector.addDirectoryAsset(root)
		if err := collectProjectSurfaces(collector, root.Path); err != nil {
			return nil, err
		}
	}

	return collector.assets(), nil
}

type projectDirectory struct {
	Path          string
	AssetType     string
	SourceSurface string
	FilePath      string
}

type parseErrorSurface struct {
	AssetClass    string
	AssetType     string
	DisplaySuffix string
}

type jsonSurfaceConfig struct {
	Path             string
	SourceSurface    string
	ProjectRoot      string
	Writable         bool
	EmitParseOnError []parseErrorSurface
	DiscoverProjects bool
}

type assetCollector struct {
	items map[string]spmapi.SyncAsset
}

func newAssetCollector() *assetCollector {
	return &assetCollector{items: make(map[string]spmapi.SyncAsset)}
}

func (c *assetCollector) add(asset spmapi.SyncAsset) {
	key := asset.Harness + "|" + asset.AssetClass + "|" + asset.AssetType + "|" + asset.IdentityKey
	c.items[key] = asset
}

func (c *assetCollector) assets() []spmapi.SyncAsset {
	items := make([]spmapi.SyncAsset, 0, len(c.items))
	for _, asset := range c.items {
		items = append(items, asset)
	}
	sort.Slice(items, func(i, j int) bool {
		left := items[i]
		right := items[j]
		if left.AssetClass != right.AssetClass {
			return left.AssetClass < right.AssetClass
		}
		if left.AssetType != right.AssetType {
			return left.AssetType < right.AssetType
		}
		return left.IdentityKey < right.IdentityKey
	})
	return items
}

func (c *assetCollector) addDirectoryAsset(root projectDirectory) {
	path := filepath.Clean(root.Path)
	c.add(spmapi.SyncAsset{
		Harness:     claudeHarness,
		AssetClass:  assetClassWorkspaceAccess,
		AssetType:   root.AssetType,
		IdentityKey: path,
		DisplayName: path,
		ContentHash: hashString(path + "|" + root.AssetType + "|" + root.SourceSurface),
		Metadata: map[string]any{
			"directory_path": path,
			"file_path":      root.FilePath,
			"source_surface": root.SourceSurface,
			"parse_status":   parseStatusOK,
		},
		Evidence: map[string]any{
			"directory_path": path,
		},
		ObservedState: map[string]any{
			"enabled": true,
		},
	})
}

func collectProjectSurfaces(collector *assetCollector, projectRoot string) error {
	surfaces := []jsonSurfaceConfig{
		{
			Path:             filepath.Join(projectRoot, ".claude", "settings.json"),
			SourceSurface:    "project_settings_json",
			ProjectRoot:      projectRoot,
			Writable:         false,
			EmitParseOnError: []parseErrorSurface{{AssetClass: assetClassPermissions, AssetType: assetTypePermissionConfig, DisplaySuffix: "permissions"}, {AssetClass: assetClassSandbox, AssetType: assetTypeSandboxConfig, DisplaySuffix: "sandbox"}},
		},
		{
			Path:             filepath.Join(projectRoot, ".claude", "settings.local.json"),
			SourceSurface:    "project_local_settings_json",
			ProjectRoot:      projectRoot,
			Writable:         true,
			EmitParseOnError: []parseErrorSurface{{AssetClass: assetClassPermissions, AssetType: assetTypePermissionConfig, DisplaySuffix: "permissions"}, {AssetClass: assetClassSandbox, AssetType: assetTypeSandboxConfig, DisplaySuffix: "sandbox"}},
		},
		{
			Path:             filepath.Join(projectRoot, ".mcp.json"),
			SourceSurface:    "project_mcp_json",
			ProjectRoot:      projectRoot,
			Writable:         false,
			EmitParseOnError: []parseErrorSurface{{AssetClass: assetClassMCPServer, AssetType: assetTypeMCPServer, DisplaySuffix: "mcp"}},
		},
	}
	for _, surface := range surfaces {
		if err := collectJSONSurface(collector, surface, nil); err != nil {
			return err
		}
	}

	instructionSurfaces := []struct {
		Path          string
		SourceSurface string
		AssetType     string
		Enforceable   bool
	}{
		{Path: filepath.Join(projectRoot, "CLAUDE.md"), SourceSurface: "project_claude_md", AssetType: assetTypeClaudeMD, Enforceable: true},
		{Path: filepath.Join(projectRoot, "CLAUDE.local.md"), SourceSurface: "project_claude_local_md", AssetType: assetTypeClaudeMD, Enforceable: true},
		{Path: filepath.Join(projectRoot, ".claude", "CLAUDE.md"), SourceSurface: "project_dot_claude_md", AssetType: assetTypeClaudeMD, Enforceable: true},
		{Path: filepath.Join(projectRoot, "AGENTS.md"), SourceSurface: "project_agents_md", AssetType: assetTypeAgentsMD, Enforceable: false},
	}
	for _, surface := range instructionSurfaces {
		if err := collectInstructionFile(collector, projectRoot, surface.Path, surface.SourceSurface, surface.AssetType, surface.Enforceable); err != nil {
			return err
		}
	}
	return nil
}

func collectJSONSurface(
	collector *assetCollector,
	cfg jsonSurfaceConfig,
	projectRoots map[string]projectDirectory,
) error {
	data, err := os.ReadFile(cfg.Path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		for _, surface := range cfg.EmitParseOnError {
			collector.addSyntheticParseError(cfg, surface, parseStatusUnreadable, err)
		}
		return nil
	}

	var doc map[string]any
	if err := json.Unmarshal(data, &doc); err != nil {
		for _, surface := range cfg.EmitParseOnError {
			collector.addSyntheticParseError(cfg, surface, parseStatusInvalid, err)
		}
		return nil
	}

	if permissionsValue, ok := pickFirst(doc, "permissions", "permissionSettings", "permissionMode"); ok {
		collector.addConfigAsset(cfg, assetClassPermissions, assetTypePermissionConfig, "permissions", permissionsValue)
	}
	if sandboxValue, ok := pickFirst(doc, "sandbox", "sandboxSettings", "sandboxMode"); ok {
		collector.addConfigAsset(cfg, assetClassSandbox, assetTypeSandboxConfig, "sandbox", sandboxValue)
	}

	for _, server := range extractMCPServers(doc, cfg) {
		collector.add(server)
	}
	for _, hook := range extractHooks(doc, cfg) {
		collector.add(hook)
	}
	for _, skill := range extractSkills(doc, cfg) {
		collector.add(skill)
	}
	for _, subagent := range extractSubagents(doc, cfg) {
		collector.add(subagent)
	}

	if cfg.DiscoverProjects && projectRoots != nil {
		for _, root := range discoverProjectDirectories(doc, cfg) {
			key := root.AssetType + "|" + root.Path
			projectRoots[key] = root
		}
	}

	return nil
}

func (c *assetCollector) addSyntheticParseError(
	cfg jsonSurfaceConfig,
	surface parseErrorSurface,
	parseStatus string,
	err error,
) {
	identity := cfg.Path + "#parse_error#" + surface.AssetType
	c.add(spmapi.SyncAsset{
		Harness:     claudeHarness,
		AssetClass:  surface.AssetClass,
		AssetType:   surface.AssetType,
		IdentityKey: identity,
		DisplayName: filepath.Base(cfg.Path) + " " + surface.DisplaySuffix,
		Metadata: map[string]any{
			"file_path":      cfg.Path,
			"project_root":   cfg.ProjectRoot,
			"source_surface": cfg.SourceSurface,
			"parse_status":   parseStatus,
			"writable":       cfg.Writable,
		},
		Evidence: map[string]any{
			"file_path":    cfg.Path,
			"parse_error":  err.Error(),
			"parse_status": parseStatus,
		},
		ObservedState: map[string]any{
			"enabled": false,
		},
	})
}

func (c *assetCollector) addConfigAsset(
	cfg jsonSurfaceConfig,
	assetClass string,
	assetType string,
	name string,
	value any,
) {
	contentHash, normalized := hashValue(value)
	identity := cfg.Path + "#" + assetType
	c.add(spmapi.SyncAsset{
		Harness:     claudeHarness,
		AssetClass:  assetClass,
		AssetType:   assetType,
		IdentityKey: identity,
		DisplayName: name + " in " + filepath.Base(cfg.Path),
		ContentHash: contentHash,
		Metadata: map[string]any{
			"file_path":      cfg.Path,
			"project_root":   cfg.ProjectRoot,
			"source_surface": cfg.SourceSurface,
			"parse_status":   parseStatusOK,
			"writable":       cfg.Writable,
		},
		Evidence: map[string]any{
			"file_path":      cfg.Path,
			"normalized":     normalized,
			"source_surface": cfg.SourceSurface,
		},
		ObservedState: map[string]any{
			"value": normalized,
		},
	})
}

func collectInstructionFile(
	collector *assetCollector,
	projectRoot string,
	path string,
	sourceSurface string,
	assetType string,
	enforceable bool,
) error {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		collector.add(spmapi.SyncAsset{
			Harness:     claudeHarness,
			AssetClass:  assetClassInstructionFile,
			AssetType:   assetType,
			IdentityKey: path,
			DisplayName: filepath.Base(path),
			Metadata: map[string]any{
				"file_path":      path,
				"project_root":   projectRoot,
				"source_surface": sourceSurface,
				"parse_status":   parseStatusUnreadable,
				"enforceable":    enforceable,
			},
			Evidence: map[string]any{
				"file_path":    path,
				"parse_error":  err.Error(),
				"parse_status": parseStatusUnreadable,
			},
		})
		return nil
	}

	text := string(data)
	indicators := extractIndicators(text)
	language := languageSignal(text)
	obfuscation := obfuscationSignals(text)

	collector.add(spmapi.SyncAsset{
		Harness:     claudeHarness,
		AssetClass:  assetClassInstructionFile,
		AssetType:   assetType,
		IdentityKey: path,
		DisplayName: filepath.Base(path),
		ContentHash: hashBytes(data),
		Metadata: map[string]any{
			"file_path":      path,
			"project_root":   projectRoot,
			"source_surface": sourceSurface,
			"parse_status":   parseStatusOK,
			"enforceable":    enforceable,
			"language":       language,
		},
		Evidence: map[string]any{
			"file_path":       path,
			"source_surface":  sourceSurface,
			"urls":            indicators.URLs,
			"domains":         indicators.Domains,
			"ips":             indicators.IPs,
			"language_signal": language,
			"obfuscation":     obfuscation,
			"content_preview": previewText(text),
			"enforceable":     enforceable,
		},
		ObservedState: map[string]any{
			"excluded": false,
		},
	})
	return nil
}

func discoverProjectDirectories(doc map[string]any, cfg jsonSurfaceConfig) []projectDirectory {
	dirs := map[string]projectDirectory{}
	for _, item := range readDirectoryEntries(doc, "trustedDirectories", "trusted_projects", "trustedProjectDirectories") {
		path := filepath.Clean(item)
		dirs[assetTypeTrustedDirectory+"|"+path] = projectDirectory{
			Path:          path,
			AssetType:     assetTypeTrustedDirectory,
			SourceSurface: cfg.SourceSurface,
			FilePath:      cfg.Path,
		}
	}
	for _, item := range readDirectoryEntries(doc, "additionalDirectories", "additional_projects", "additionalProjectDirectories") {
		path := filepath.Clean(item)
		dirs[assetTypeAdditionalDirectory+"|"+path] = projectDirectory{
			Path:          path,
			AssetType:     assetTypeAdditionalDirectory,
			SourceSurface: cfg.SourceSurface,
			FilePath:      cfg.Path,
		}
	}

	if rawProjects, ok := doc["projects"]; ok {
		if projects, ok := rawProjects.(map[string]any); ok {
			for projectPath, rawValue := range projects {
				path := filepath.Clean(projectPath)
				assetType := assetTypeTrustedDirectory
				if projectType := classifyProjectValue(rawValue); projectType != "" {
					assetType = projectType
				}
				dirs[assetType+"|"+path] = projectDirectory{
					Path:          path,
					AssetType:     assetType,
					SourceSurface: cfg.SourceSurface,
					FilePath:      cfg.Path,
				}
			}
		}
	}

	items := make([]projectDirectory, 0, len(dirs))
	for _, item := range dirs {
		items = append(items, item)
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].AssetType == items[j].AssetType {
			return items[i].Path < items[j].Path
		}
		return items[i].AssetType < items[j].AssetType
	})
	return items
}

func classifyProjectValue(raw any) string {
	project, ok := raw.(map[string]any)
	if !ok {
		return ""
	}
	if trustLevel, ok := stringValue(project["trustLevel"]); ok {
		switch strings.ToLower(trustLevel) {
		case "additional":
			return assetTypeAdditionalDirectory
		case "trusted":
			return assetTypeTrustedDirectory
		}
	}
	if trusted, ok := boolValue(project["trusted"]); ok && trusted {
		return assetTypeTrustedDirectory
	}
	if additional, ok := boolValue(project["additional"]); ok && additional {
		return assetTypeAdditionalDirectory
	}
	return ""
}

func extractMCPServers(doc map[string]any, cfg jsonSurfaceConfig) []spmapi.SyncAsset {
	raw, ok := pickFirst(doc, "mcpServers", "mcp_servers")
	if !ok {
		return nil
	}

	assets := []spmapi.SyncAsset{}
	switch servers := raw.(type) {
	case map[string]any:
		for serverName, rawServer := range servers {
			if asset, ok := buildMCPAsset(cfg, serverName, rawServer); ok {
				assets = append(assets, asset)
			}
		}
	case []any:
		for index, rawServer := range servers {
			server, ok := rawServer.(map[string]any)
			if !ok {
				continue
			}
			serverName, ok := pickString(server, "name", "serverName")
			if !ok {
				serverName = fmt.Sprintf("server-%d", index)
			}
			if asset, ok := buildMCPAsset(cfg, serverName, rawServer); ok {
				assets = append(assets, asset)
			}
		}
	}

	sort.Slice(assets, func(i, j int) bool {
		return assets[i].IdentityKey < assets[j].IdentityKey
	})
	return assets
}

func buildMCPAsset(cfg jsonSurfaceConfig, serverName string, raw any) (spmapi.SyncAsset, bool) {
	server, ok := raw.(map[string]any)
	if !ok {
		return spmapi.SyncAsset{}, false
	}

	resolvedIdentity, transport, identityEvidence := resolveMCPIdentity(server)
	disabled, _ := boolValue(server["disabled"])
	contentHash, normalized := hashValue(server)
	identityKey := cfg.Path + "#mcp:" + serverName + "|" + resolvedIdentity

	metadata := map[string]any{
		"file_path":         cfg.Path,
		"project_root":      cfg.ProjectRoot,
		"source_surface":    cfg.SourceSurface,
		"parse_status":      parseStatusOK,
		"writable":          cfg.Writable,
		"server_name":       serverName,
		"resolved_identity": resolvedIdentity,
		"transport":         transport,
		"mcp_identity_key":  serverName + "|" + resolvedIdentity,
		"approval_identity": map[string]any{"server_name": serverName, "resolved_identity": resolvedIdentity},
	}
	for key, value := range identityEvidence {
		metadata[key] = value
	}

	evidence := map[string]any{
		"file_path":         cfg.Path,
		"server_name":       serverName,
		"resolved_identity": resolvedIdentity,
		"transport":         transport,
		"config":            normalized,
	}
	for key, value := range indicatorsFromServer(server) {
		evidence[key] = value
	}

	return spmapi.SyncAsset{
		Harness:     claudeHarness,
		AssetClass:  assetClassMCPServer,
		AssetType:   assetTypeMCPServer,
		IdentityKey: identityKey,
		DisplayName: serverName,
		ContentHash: contentHash,
		Metadata:    metadata,
		Evidence:    evidence,
		ObservedState: map[string]any{
			"disabled": disabled,
		},
	}, true
}

func extractHooks(doc map[string]any, cfg jsonSurfaceConfig) []spmapi.SyncAsset {
	raw, ok := doc["hooks"]
	if !ok {
		return nil
	}
	assets := []spmapi.SyncAsset{}
	hooks, ok := raw.(map[string]any)
	if !ok {
		return nil
	}
	for eventName, eventValue := range hooks {
		for index, hook := range normalizeHookEntries(eventValue) {
			fingerprint := fmt.Sprintf("%s|%s|%s|%d", eventName, hook.Matcher, hook.Command, index)
			contentHash, normalized := hashValue(hook.Raw)
			assets = append(assets, spmapi.SyncAsset{
				Harness:     claudeHarness,
				AssetClass:  assetClassExtension,
				AssetType:   assetTypeHook,
				IdentityKey: cfg.Path + "#hook:" + fingerprint,
				DisplayName: hookDisplayName(eventName, hook.Matcher, hook.Command),
				ContentHash: contentHash,
				Metadata: map[string]any{
					"file_path":      cfg.Path,
					"project_root":   cfg.ProjectRoot,
					"source_surface": cfg.SourceSurface,
					"parse_status":   parseStatusOK,
					"writable":       cfg.Writable,
					"event":          eventName,
					"matcher":        hook.Matcher,
					"command":        hook.Command,
					"fingerprint":    fingerprint,
				},
				Evidence: map[string]any{
					"file_path": cfg.Path,
					"hook":      normalized,
				},
				ObservedState: map[string]any{
					"disabled": false,
				},
			})
		}
	}
	sort.Slice(assets, func(i, j int) bool {
		return assets[i].IdentityKey < assets[j].IdentityKey
	})
	return assets
}

type hookEntry struct {
	Matcher string
	Command string
	Raw     any
}

func normalizeHookEntries(raw any) []hookEntry {
	items := []hookEntry{}
	switch value := raw.(type) {
	case []any:
		for _, item := range value {
			items = append(items, normalizeHookEntries(item)...)
		}
	case map[string]any:
		matcher, _ := pickString(value, "matcher", "name")
		command, _ := pickString(value, "command", "cmd")
		if command == "" {
			if commands, ok := value["commands"].([]any); ok {
				parts := make([]string, 0, len(commands))
				for _, commandPart := range commands {
					if text, ok := stringValue(commandPart); ok {
						parts = append(parts, text)
					}
				}
				command = strings.Join(parts, " ")
			}
		}
		items = append(items, hookEntry{Matcher: matcher, Command: command, Raw: value})
	}
	return items
}

func hookDisplayName(eventName string, matcher string, command string) string {
	name := eventName
	if matcher != "" {
		name += " " + matcher
	}
	if command != "" {
		name += " " + command
	}
	return strings.TrimSpace(name)
}

func extractSkills(doc map[string]any, cfg jsonSurfaceConfig) []spmapi.SyncAsset {
	raw, ok := doc["skills"]
	if !ok {
		return nil
	}
	assets := []spmapi.SyncAsset{}
	for _, skill := range normalizeNamedEntries(raw) {
		contentHash, normalized := hashValue(skill.Raw)
		assets = append(assets, spmapi.SyncAsset{
			Harness:     claudeHarness,
			AssetClass:  assetClassSkill,
			AssetType:   assetTypeSkill,
			IdentityKey: cfg.Path + "#skill:" + skill.Fingerprint,
			DisplayName: skill.Name,
			ContentHash: contentHash,
			Metadata: map[string]any{
				"file_path":      cfg.Path,
				"project_root":   cfg.ProjectRoot,
				"source_surface": cfg.SourceSurface,
				"parse_status":   parseStatusOK,
				"writable":       cfg.Writable,
				"fingerprint":    skill.Fingerprint,
				"name":           skill.Name,
			},
			Evidence: map[string]any{
				"file_path": cfg.Path,
				"skill":     normalized,
			},
			ObservedState: map[string]any{
				"disabled": false,
			},
		})
	}
	sort.Slice(assets, func(i, j int) bool {
		return assets[i].IdentityKey < assets[j].IdentityKey
	})
	return assets
}

func extractSubagents(doc map[string]any, cfg jsonSurfaceConfig) []spmapi.SyncAsset {
	raw, ok := doc["subagents"]
	if !ok {
		return nil
	}
	assets := []spmapi.SyncAsset{}
	for _, item := range normalizeNamedEntries(raw) {
		contentHash, normalized := hashValue(item.Raw)
		assets = append(assets, spmapi.SyncAsset{
			Harness:     claudeHarness,
			AssetClass:  assetClassAgent,
			AssetType:   assetTypeSubagent,
			IdentityKey: cfg.Path + "#subagent:" + item.Fingerprint,
			DisplayName: item.Name,
			ContentHash: contentHash,
			Metadata: map[string]any{
				"file_path":      cfg.Path,
				"project_root":   cfg.ProjectRoot,
				"source_surface": cfg.SourceSurface,
				"parse_status":   parseStatusOK,
				"writable":       cfg.Writable,
				"fingerprint":    item.Fingerprint,
				"name":           item.Name,
			},
			Evidence: map[string]any{
				"file_path": cfg.Path,
				"subagent":  normalized,
			},
			ObservedState: map[string]any{
				"disabled": false,
			},
		})
	}
	sort.Slice(assets, func(i, j int) bool {
		return assets[i].IdentityKey < assets[j].IdentityKey
	})
	return assets
}

type namedEntry struct {
	Name        string
	Fingerprint string
	Raw         any
}

func normalizeNamedEntries(raw any) []namedEntry {
	items := []namedEntry{}
	switch value := raw.(type) {
	case []any:
		for index, item := range value {
			switch itemValue := item.(type) {
			case string:
				items = append(items, namedEntry{
					Name:        itemValue,
					Fingerprint: itemValue,
					Raw:         item,
				})
			case map[string]any:
				name, _ := pickString(itemValue, "name", "path", "id")
				if name == "" {
					name = fmt.Sprintf("item-%d", index)
				}
				items = append(items, namedEntry{
					Name:        name,
					Fingerprint: hashString(name + "|" + mustJSON(itemValue)),
					Raw:         itemValue,
				})
			}
		}
	case map[string]any:
		for name, item := range value {
			items = append(items, namedEntry{
				Name:        name,
				Fingerprint: hashString(name + "|" + mustJSON(item)),
				Raw:         item,
			})
		}
	}
	sort.Slice(items, func(i, j int) bool {
		return items[i].Name < items[j].Name
	})
	return items
}

type indicatorSet struct {
	URLs    []string
	Domains []string
	IPs     []string
}

func extractIndicators(text string) indicatorSet {
	urls := uniqueStrings(urlPattern.FindAllString(text, -1))
	domains := uniqueStrings(domainPattern.FindAllString(text, -1))
	ips := []string{}
	for _, candidate := range ipv4Pattern.FindAllString(text, -1) {
		if ip := net.ParseIP(candidate); ip != nil {
			ips = append(ips, candidate)
		}
	}
	for _, candidate := range ipv6Pattern.FindAllString(text, -1) {
		if ip := net.ParseIP(candidate); ip != nil {
			ips = append(ips, candidate)
		}
	}
	return indicatorSet{
		URLs:    urls,
		Domains: domains,
		IPs:     uniqueStrings(ips),
	}
}

func indicatorsFromServer(server map[string]any) map[string]any {
	payload := mustJSON(server)
	indicators := extractIndicators(payload)
	return map[string]any{
		"urls":    indicators.URLs,
		"domains": indicators.Domains,
		"ips":     indicators.IPs,
	}
}

func languageSignal(text string) map[string]any {
	asciiLetters := 0
	nonASCII := 0
	totalLetters := 0
	for _, r := range text {
		if r > 127 {
			nonASCII++
		}
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') {
			totalLetters++
			if r <= 127 {
				asciiLetters++
			}
		}
	}
	asciiRatio := 1.0
	if len(text) > 0 {
		asciiRatio = float64(len(text)-nonASCII) / float64(len(text))
	}
	englishHint := strings.Contains(strings.ToLower(text), " the ") ||
		strings.Contains(strings.ToLower(text), " and ") ||
		strings.Contains(strings.ToLower(text), " use ")
	likelyEnglish := asciiRatio >= 0.9 && (totalLetters == 0 || float64(asciiLetters)/math.Max(float64(totalLetters), 1) >= 0.95) && englishHint
	return map[string]any{
		"ascii_ratio":       roundFloat(asciiRatio),
		"likely_english":    likelyEnglish,
		"has_non_ascii":     nonASCII > 0,
		"english_hint_seen": englishHint,
	}
}

func obfuscationSignals(text string) map[string]any {
	base64Matches := []string{}
	for _, candidate := range base64CandidatePattern.FindAllString(text, -1) {
		if _, err := hex.DecodeString(candidate); err == nil {
			continue
		}
		base64Matches = append(base64Matches, candidate)
	}

	highEntropy := []string{}
	for _, candidate := range tokenPattern.FindAllString(text, -1) {
		if entropy(candidate) >= 4.1 {
			highEntropy = append(highEntropy, candidate)
		}
	}

	defanged := strings.Contains(strings.ToLower(text), "hxxp") || strings.Contains(text, "[.]")
	return map[string]any{
		"base64_like_count":        len(uniqueStrings(base64Matches)),
		"base64_like_samples":      shortenList(uniqueStrings(base64Matches), 3),
		"high_entropy_count":       len(uniqueStrings(highEntropy)),
		"high_entropy_samples":     shortenList(uniqueStrings(highEntropy), 3),
		"defanged_indicator_found": defanged,
		"obfuscation_detected":     len(base64Matches) > 0 || len(highEntropy) > 0 || defanged,
	}
}

func resolveMCPIdentity(server map[string]any) (string, string, map[string]any) {
	if rawURL, ok := pickString(server, "url", "endpoint"); ok {
		normalized, origin := normalizeURLIdentity(rawURL)
		return normalized, "http", map[string]any{
			"url":             normalized,
			"origin":          origin,
			"resolved_url":    normalized,
			"resolved_origin": origin,
		}
	}

	command, _ := pickString(server, "command", "cmd")
	args := stringSlice(server["args"])
	identity := normalizeStdioIdentity(command, args)
	return identity, "stdio", map[string]any{
		"command":          command,
		"args":             args,
		"resolved_command": identity,
	}
}

func normalizeURLIdentity(raw string) (string, string) {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return strings.TrimSpace(raw), strings.TrimSpace(raw)
	}
	parsed.Fragment = ""
	parsed.RawFragment = ""
	parsed.RawQuery = ""
	parsed.Host = strings.ToLower(parsed.Hostname())
	if port := parsed.Port(); port != "" && !isDefaultPort(parsed.Scheme, port) {
		parsed.Host = parsed.Host + ":" + port
	}
	cleanPath := filepath.Clean(parsed.EscapedPath())
	if cleanPath == "." || cleanPath == "/" {
		parsed.Path = ""
	} else {
		parsed.Path = cleanPath
	}
	origin := parsed.Scheme + "://" + parsed.Host
	if parsed.Path == "" {
		return origin, origin
	}
	return origin + parsed.Path, origin
}

func normalizeStdioIdentity(command string, args []string) string {
	cmd := strings.TrimSpace(command)
	base := filepath.Base(cmd)
	packageManagers := map[string]bool{
		"npx":  true,
		"npm":  true,
		"pnpm": true,
		"yarn": true,
		"uvx":  true,
		"pipx": true,
	}
	if packageManagers[base] {
		for _, arg := range args {
			if strings.HasPrefix(arg, "-") || strings.TrimSpace(arg) == "" {
				continue
			}
			return "package:" + arg
		}
	}
	if base == "node" || base == "python" || base == "python3" {
		for _, arg := range args {
			if strings.HasPrefix(arg, "-") || strings.TrimSpace(arg) == "" {
				continue
			}
			return "binary:" + filepath.Base(arg)
		}
	}
	if base == "" {
		return "binary:unknown"
	}
	return "binary:" + base
}

func isDefaultPort(scheme string, port string) bool {
	return (scheme == "http" && port == "80") || (scheme == "https" && port == "443")
}

func readDirectoryEntries(doc map[string]any, keys ...string) []string {
	entries := []string{}
	for _, key := range keys {
		raw, ok := doc[key]
		if !ok {
			continue
		}
		entries = append(entries, stringSlice(raw)...)
	}
	return uniqueStrings(entries)
}

func pickFirst(doc map[string]any, keys ...string) (any, bool) {
	for _, key := range keys {
		if value, ok := doc[key]; ok {
			return value, true
		}
	}
	return nil, false
}

func pickString(doc map[string]any, keys ...string) (string, bool) {
	for _, key := range keys {
		if value, ok := stringValue(doc[key]); ok {
			return value, true
		}
	}
	return "", false
}

func stringValue(raw any) (string, bool) {
	value, ok := raw.(string)
	if !ok {
		return "", false
	}
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return "", false
	}
	return trimmed, true
}

func boolValue(raw any) (bool, bool) {
	value, ok := raw.(bool)
	return value, ok
}

func stringSlice(raw any) []string {
	switch value := raw.(type) {
	case []string:
		return uniqueStrings(value)
	case []any:
		items := make([]string, 0, len(value))
		for _, item := range value {
			if text, ok := stringValue(item); ok {
				items = append(items, text)
			}
		}
		return uniqueStrings(items)
	default:
		return nil
	}
}

func hashBytes(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func hashString(value string) string {
	return hashBytes([]byte(value))
}

func hashValue(value any) (string, any) {
	data, err := json.Marshal(value)
	if err != nil {
		return hashString(fmt.Sprintf("%v", value)), value
	}
	var normalized any
	if err := json.Unmarshal(data, &normalized); err != nil {
		normalized = value
	}
	return hashBytes(data), normalized
}

func mustJSON(value any) string {
	data, err := json.Marshal(value)
	if err != nil {
		return fmt.Sprintf("%v", value)
	}
	return string(data)
}

func uniqueStrings(items []string) []string {
	seen := map[string]struct{}{}
	result := make([]string, 0, len(items))
	for _, item := range items {
		if item == "" {
			continue
		}
		if _, ok := seen[item]; ok {
			continue
		}
		seen[item] = struct{}{}
		result = append(result, item)
	}
	sort.Strings(result)
	return result
}

func shortenList(items []string, limit int) []string {
	if len(items) <= limit {
		return items
	}
	return items[:limit]
}

func previewText(text string) string {
	trimmed := strings.TrimSpace(text)
	if len(trimmed) <= 240 {
		return trimmed
	}
	return trimmed[:240]
}

func entropy(value string) float64 {
	if value == "" {
		return 0
	}
	counts := map[rune]float64{}
	for _, r := range value {
		counts[r]++
	}
	total := float64(len(value))
	sum := 0.0
	for _, count := range counts {
		p := count / total
		sum -= p * math.Log2(p)
	}
	return sum
}

func roundFloat(value float64) float64 {
	return math.Round(value*1000) / 1000
}
