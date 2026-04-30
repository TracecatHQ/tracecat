package inventory

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/claude"
	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/spmapi"
)

const (
	claudeHarness = "claude_code"

	itemTypeTrustedDirectory    = "trusted_directory"
	itemTypeAdditionalDirectory = "additional_directory"
	itemTypePermissionConfig    = "permission_config"
	itemTypeSandboxConfig       = "sandbox_config"
	itemTypeMCPServer           = "mcp_server"
	itemTypeSkill               = "skill"
	itemTypeHook                = "hook"
	itemTypeInstructionFile     = "instruction_file"
	itemTypeSubagent            = "subagent"
	itemTypePlugin              = "plugin"
	itemTypeCommand             = "command"
	itemTypeLSPServer           = "lsp_server"
	itemTypeMonitor             = "monitor"
	itemTypeBinary              = "binary"
	itemTypePluginSettings      = "plugin_settings"
	itemTypeOutputStyle         = "output_style"
	itemTypeTheme               = "theme"

	sourceTypeSettingsJSON        = "settings_json"
	sourceTypeSettingsLocalJSON   = "settings_local_json"
	sourceTypeClaudeJSON          = "claude_json"
	sourceTypeHooksJSON           = "hooks_json"
	sourceTypeMCPJSON             = "mcp_json"
	sourceTypeLSPJSON             = "lsp_json"
	sourceTypeMonitorsJSON        = "monitors_json"
	sourceTypePluginSettingsJSON  = "plugin_settings_json"
	sourceTypeClaudeMD            = "claude_md"
	sourceTypeClaudeLocalMD       = "claude_local_md"
	sourceTypeAgentsMD            = "agents_md"
	sourceTypeSkillFrontmatter    = "skill_frontmatter"
	sourceTypeSubagentFrontmatter = "subagent_frontmatter"
	sourceTypePluginManifest      = "plugin_manifest"
	sourceTypeCommandFile         = "command_file"
	sourceTypeBinaryFile          = "binary_file"
	sourceTypeOutputStyleFile     = "output_style_file"
	sourceTypeThemeFile           = "theme_file"
	sourceTypeDirectory           = "directory"

	parseStatusOK         = "ok"
	parseStatusInvalid    = "invalid"
	parseStatusUnreadable = "unreadable"

	relationshipTypeDefines = "defines"
)

var (
	urlPattern             = regexp.MustCompile(`https?://[^\s<>"')\]]+`)
	domainPattern          = regexp.MustCompile(`(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\b`)
	ipv4Pattern            = regexp.MustCompile(`\b(?:\d{1,3}\.){3}\d{1,3}\b`)
	ipv6Pattern            = regexp.MustCompile(`(?i)\b(?:[a-f0-9]{1,4}:){2,}[a-f0-9]{1,4}\b`)
	base64CandidatePattern = regexp.MustCompile(`\b[A-Za-z0-9+/]{24,}={0,2}\b`)
	tokenPattern           = regexp.MustCompile(`\b[A-Za-z0-9+/=_-]{20,}\b`)
)

var allowedInventoryBindings = map[string]map[string]struct{}{
	itemTypeHook: {
		sourceTypeSettingsJSON:      {},
		sourceTypeSettingsLocalJSON: {},
		sourceTypeClaudeJSON:        {},
		sourceTypeHooksJSON:         {},
	},
	itemTypePlugin: {
		sourceTypePluginManifest: {},
	},
	itemTypeMCPServer: {
		sourceTypeSettingsJSON:      {},
		sourceTypeSettingsLocalJSON: {},
		sourceTypeClaudeJSON:        {},
		sourceTypeMCPJSON:           {},
	},
	itemTypeLSPServer: {
		sourceTypeLSPJSON: {},
	},
	itemTypeInstructionFile: {
		sourceTypeClaudeMD:      {},
		sourceTypeClaudeLocalMD: {},
		sourceTypeAgentsMD:      {},
	},
	itemTypePermissionConfig: {
		sourceTypeSettingsJSON:      {},
		sourceTypeSettingsLocalJSON: {},
		sourceTypeClaudeJSON:        {},
	},
	itemTypeSandboxConfig: {
		sourceTypeSettingsJSON:      {},
		sourceTypeSettingsLocalJSON: {},
		sourceTypeClaudeJSON:        {},
	},
	itemTypeTrustedDirectory: {
		sourceTypeDirectory: {},
	},
	itemTypeAdditionalDirectory: {
		sourceTypeDirectory: {},
	},
	itemTypeSkill: {
		sourceTypeSettingsJSON:      {},
		sourceTypeSettingsLocalJSON: {},
		sourceTypeClaudeJSON:        {},
		sourceTypeSkillFrontmatter:  {},
	},
	itemTypeSubagent: {
		sourceTypeSettingsJSON:        {},
		sourceTypeSettingsLocalJSON:   {},
		sourceTypeClaudeJSON:          {},
		sourceTypeSubagentFrontmatter: {},
	},
	itemTypeCommand: {
		sourceTypeCommandFile: {},
	},
	itemTypeMonitor: {
		sourceTypeMonitorsJSON: {},
	},
	itemTypeBinary: {
		sourceTypeBinaryFile: {},
	},
	itemTypePluginSettings: {
		sourceTypePluginSettingsJSON: {},
	},
	itemTypeOutputStyle: {
		sourceTypeOutputStyleFile: {},
	},
	itemTypeTheme: {
		sourceTypeThemeFile: {},
	},
}

func validateInventoryItem(item spmapi.SyncInventoryItem) error {
	if item.Harness != claudeHarness {
		return fmt.Errorf("unsupported inventory harness %q", item.Harness)
	}
	sourceTypes, ok := allowedInventoryBindings[item.ItemType]
	if !ok {
		return fmt.Errorf("unsupported inventory item type %q", item.ItemType)
	}
	if _, ok := sourceTypes[item.SourceType]; !ok {
		return fmt.Errorf("invalid inventory item/source binding %s/%s", item.ItemType, item.SourceType)
	}
	if item.SourceLocation == "" {
		return fmt.Errorf("inventory source location is required")
	}
	if item.ItemLocation == "" {
		return fmt.Errorf("inventory item location is required")
	}
	return nil
}

// Provider returns the current local inventory for sync requests.
type Provider interface {
	Collect(context.Context) (spmapi.InventorySnapshot, error)
}

// ClaudeProvider inventories Claude Code user and project surfaces.
type ClaudeProvider struct {
	HomeDir string
}

func NewClaudeProvider(homeDir string) ClaudeProvider {
	return ClaudeProvider{HomeDir: strings.TrimSpace(homeDir)}
}

func (p ClaudeProvider) Collect(ctx context.Context) (spmapi.InventorySnapshot, error) {
	if err := ctx.Err(); err != nil {
		return spmapi.InventorySnapshot{}, err
	}
	if p.HomeDir == "" {
		return spmapi.InventorySnapshot{}, fmt.Errorf("home directory is required")
	}

	collector := newInventoryCollector()
	projectRoots := map[string]projectDirectory{}

	userSettingsPath := filepath.Join(p.HomeDir, ".claude", "settings.json")
	if err := collectJSONSurface(collector, jsonSurfaceConfig{
		Path:          userSettingsPath,
		SourceType:    sourceTypeSettingsJSON,
		SourceSurface: "user_settings_json",
		ProjectRoot:   "",
		Writable:      true,
		EmitParseOnError: []parseErrorSurface{
			{ItemType: itemTypePermissionConfig, DisplaySuffix: "permissions"},
			{ItemType: itemTypeSandboxConfig, DisplaySuffix: "sandbox"},
			{ItemType: itemTypeMCPServer, DisplaySuffix: "mcp"},
			{ItemType: itemTypeHook, DisplaySuffix: "hooks"},
		},
		DiscoverProjects: true,
	}, projectRoots); err != nil {
		return spmapi.InventorySnapshot{}, err
	}

	userStatePath := filepath.Join(p.HomeDir, ".claude.json")
	if err := collectJSONSurface(collector, jsonSurfaceConfig{
		Path:          userStatePath,
		SourceType:    sourceTypeClaudeJSON,
		SourceSurface: "user_state_json",
		ProjectRoot:   "",
		Writable:      true,
		EmitParseOnError: []parseErrorSurface{
			{ItemType: itemTypePermissionConfig, DisplaySuffix: "permissions"},
			{ItemType: itemTypeSandboxConfig, DisplaySuffix: "sandbox"},
			{ItemType: itemTypeMCPServer, DisplaySuffix: "mcp"},
			{ItemType: itemTypeHook, DisplaySuffix: "hooks"},
		},
		DiscoverProjects: true,
	}, projectRoots); err != nil {
		return spmapi.InventorySnapshot{}, err
	}
	if err := collectComponentSurfaces(collector, filepath.Join(p.HomeDir, ".claude"), "", "user", ""); err != nil {
		return spmapi.InventorySnapshot{}, err
	}
	if err := collectPluginSurfaces(collector, p.HomeDir); err != nil {
		return spmapi.InventorySnapshot{}, err
	}

	roots := make([]projectDirectory, 0, len(projectRoots))
	for _, root := range projectRoots {
		roots = append(roots, root)
	}
	sort.Slice(roots, func(i, j int) bool {
		if roots[i].ItemType == roots[j].ItemType {
			return roots[i].Path < roots[j].Path
		}
		return roots[i].ItemType < roots[j].ItemType
	})

	for _, root := range roots {
		if err := ctx.Err(); err != nil {
			return spmapi.InventorySnapshot{}, err
		}
		collector.addDirectoryItem(root)
		if err := collectProjectSurfaces(collector, root.Path); err != nil {
			return spmapi.InventorySnapshot{}, err
		}
	}

	return collector.snapshot()
}

type projectDirectory struct {
	Path          string
	ItemType      string
	SourceSurface string
	FilePath      string
}

type parseErrorSurface struct {
	ItemType      string
	DisplaySuffix string
}

type jsonSurfaceConfig struct {
	Path              string
	SourceType        string
	SourceSurface     string
	ProjectRoot       string
	Writable          bool
	ParentIdentityKey string
	ParentRelation    string
	EmitParseOnError  []parseErrorSurface
	DiscoverProjects  bool
}

type inventoryCollector struct {
	items         map[string]spmapi.SyncInventoryItem
	relationships map[string]spmapi.SyncInventoryRelationship
	err           error
}

func newInventoryCollector() *inventoryCollector {
	return &inventoryCollector{
		items:         make(map[string]spmapi.SyncInventoryItem),
		relationships: make(map[string]spmapi.SyncInventoryRelationship),
	}
}

func (c *inventoryCollector) add(item spmapi.SyncInventoryItem) {
	if c.err != nil {
		return
	}
	if item.ItemLocation == "" {
		item.ItemLocation = item.SourceLocation
	}
	if err := validateInventoryItem(item); err != nil {
		c.err = err
		return
	}
	key := item.Harness + "|" + item.ItemType + "|" + item.SourceType + "|" + item.ItemLocation + "|" + item.SourceLocation + "|" + item.IdentityKey
	c.items[key] = item
}

func (c *inventoryCollector) addRelationship(relationship spmapi.SyncInventoryRelationship) {
	if relationship.FromIdentityKey == "" || relationship.ToIdentityKey == "" {
		return
	}
	if relationship.RelationshipType != relationshipTypeDefines {
		c.err = fmt.Errorf("unsupported inventory relationship type %q", relationship.RelationshipType)
		return
	}
	key := relationship.RelationshipType + "|" + relationship.FromIdentityKey + "|" + relationship.ToIdentityKey
	c.relationships[key] = relationship
}

func (c *inventoryCollector) addParentRelationship(
	parentIdentityKey string,
	relationshipType string,
	child spmapi.SyncInventoryItem,
) {
	if parentIdentityKey == "" || relationshipType == "" {
		return
	}
	c.addRelationship(spmapi.SyncInventoryRelationship{
		RelationshipType: relationshipType,
		FromIdentityKey:  parentIdentityKey,
		ToIdentityKey:    child.IdentityKey,
		Evidence: map[string]any{
			"source_location": child.SourceLocation,
			"item_type":       child.ItemType,
			"source_type":     child.SourceType,
		},
		ObservedState: map[string]any{
			"enabled": true,
		},
	})
}

func (c *inventoryCollector) snapshot() (spmapi.InventorySnapshot, error) {
	if c.err != nil {
		return spmapi.InventorySnapshot{}, c.err
	}
	return spmapi.InventorySnapshot{
		InventoryItems: c.inventoryItems(),
		Relationships:  c.validRelationships(),
	}, nil
}

func (c *inventoryCollector) inventoryItems() []spmapi.SyncInventoryItem {
	items := make([]spmapi.SyncInventoryItem, 0, len(c.items))
	for _, item := range c.items {
		items = append(items, item)
	}
	sort.Slice(items, func(i, j int) bool {
		left := items[i]
		right := items[j]
		if left.ItemType != right.ItemType {
			return left.ItemType < right.ItemType
		}
		if left.SourceType != right.SourceType {
			return left.SourceType < right.SourceType
		}
		if left.SourceLocation != right.SourceLocation {
			return left.SourceLocation < right.SourceLocation
		}
		return left.IdentityKey < right.IdentityKey
	})
	return items
}

func (c *inventoryCollector) validRelationships() []spmapi.SyncInventoryRelationship {
	identityKeys := make(map[string]struct{}, len(c.items))
	for _, item := range c.items {
		identityKeys[item.IdentityKey] = struct{}{}
	}
	relationships := make([]spmapi.SyncInventoryRelationship, 0, len(c.relationships))
	for _, relationship := range c.relationships {
		if _, ok := identityKeys[relationship.FromIdentityKey]; !ok {
			continue
		}
		if _, ok := identityKeys[relationship.ToIdentityKey]; !ok {
			continue
		}
		relationships = append(relationships, relationship)
	}
	sort.Slice(relationships, func(i, j int) bool {
		if relationships[i].RelationshipType != relationships[j].RelationshipType {
			return relationships[i].RelationshipType < relationships[j].RelationshipType
		}
		if relationships[i].FromIdentityKey != relationships[j].FromIdentityKey {
			return relationships[i].FromIdentityKey < relationships[j].FromIdentityKey
		}
		return relationships[i].ToIdentityKey < relationships[j].ToIdentityKey
	})
	return relationships
}

func (c *inventoryCollector) addDirectoryItem(root projectDirectory) {
	path := filepath.Clean(root.Path)
	sourceLocation := root.FilePath
	if sourceLocation == "" {
		sourceLocation = path
	}
	c.add(spmapi.SyncInventoryItem{
		Harness:        claudeHarness,
		ItemType:       root.ItemType,
		SourceType:     sourceTypeDirectory,
		ItemLocation:   path,
		SourceLocation: sourceLocation,
		IdentityKey:    path,
		DisplayName:    path,
		ContentHash:    hashString(path + "|" + root.ItemType + "|" + root.SourceSurface),
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

func collectProjectSurfaces(collector *inventoryCollector, projectRoot string) error {
	surfaces := []jsonSurfaceConfig{
		{
			Path:             filepath.Join(projectRoot, ".claude", "settings.json"),
			SourceType:       sourceTypeSettingsJSON,
			SourceSurface:    "project_settings_json",
			ProjectRoot:      projectRoot,
			Writable:         false,
			EmitParseOnError: []parseErrorSurface{{ItemType: itemTypePermissionConfig, DisplaySuffix: "permissions"}, {ItemType: itemTypeSandboxConfig, DisplaySuffix: "sandbox"}, {ItemType: itemTypeMCPServer, DisplaySuffix: "mcp"}, {ItemType: itemTypeHook, DisplaySuffix: "hooks"}},
		},
		{
			Path:             filepath.Join(projectRoot, ".claude", "settings.local.json"),
			SourceType:       sourceTypeSettingsLocalJSON,
			SourceSurface:    "project_local_settings_json",
			ProjectRoot:      projectRoot,
			Writable:         true,
			EmitParseOnError: []parseErrorSurface{{ItemType: itemTypePermissionConfig, DisplaySuffix: "permissions"}, {ItemType: itemTypeSandboxConfig, DisplaySuffix: "sandbox"}, {ItemType: itemTypeMCPServer, DisplaySuffix: "mcp"}, {ItemType: itemTypeHook, DisplaySuffix: "hooks"}},
		},
		{
			Path:             filepath.Join(projectRoot, ".mcp.json"),
			SourceType:       sourceTypeMCPJSON,
			SourceSurface:    "project_mcp_json",
			ProjectRoot:      projectRoot,
			Writable:         false,
			EmitParseOnError: []parseErrorSurface{{ItemType: itemTypeMCPServer, DisplaySuffix: "mcp"}},
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
		SourceType    string
		Enforceable   bool
	}{
		{Path: filepath.Join(projectRoot, "CLAUDE.md"), SourceSurface: "project_claude_md", SourceType: sourceTypeClaudeMD, Enforceable: true},
		{Path: filepath.Join(projectRoot, "CLAUDE.local.md"), SourceSurface: "project_claude_local_md", SourceType: sourceTypeClaudeLocalMD, Enforceable: true},
		{Path: filepath.Join(projectRoot, ".claude", "CLAUDE.md"), SourceSurface: "project_dot_claude_md", SourceType: sourceTypeClaudeMD, Enforceable: true},
		{Path: filepath.Join(projectRoot, "AGENTS.md"), SourceSurface: "project_agents_md", SourceType: sourceTypeAgentsMD, Enforceable: false},
	}
	for _, surface := range instructionSurfaces {
		if err := collectInstructionFile(collector, projectRoot, surface.Path, surface.SourceSurface, surface.SourceType, surface.Enforceable); err != nil {
			return err
		}
	}
	if err := collectComponentSurfaces(collector, filepath.Join(projectRoot, ".claude"), projectRoot, "project", ""); err != nil {
		return err
	}
	return nil
}

func collectJSONSurface(
	collector *inventoryCollector,
	cfg jsonSurfaceConfig,
	projectRoots map[string]projectDirectory,
) error {
	data, err := os.ReadFile(cfg.Path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		for _, surface := range cfg.EmitParseOnError {
			item := collector.addSyntheticParseError(cfg, surface, parseStatusUnreadable, err)
			collector.addParentRelationship(cfg.ParentIdentityKey, cfg.ParentRelation, item)
		}
		return nil
	}

	var doc map[string]any
	if err := json.Unmarshal(data, &doc); err != nil {
		for _, surface := range cfg.EmitParseOnError {
			item := collector.addSyntheticParseError(cfg, surface, parseStatusInvalid, err)
			collector.addParentRelationship(cfg.ParentIdentityKey, cfg.ParentRelation, item)
		}
		return nil
	}

	if permissionsValue, ok := pickFirst(doc, "permissions", "permissionSettings", "permissionMode"); ok {
		collector.addConfigItem(cfg, itemTypePermissionConfig, "permissions", permissionsValue)
	}
	if sandboxValue, ok := pickFirst(doc, "sandbox", "sandboxSettings", "sandboxMode"); ok {
		collector.addConfigItem(cfg, itemTypeSandboxConfig, "sandbox", sandboxValue)
	}

	for _, server := range extractMCPServers(doc, cfg) {
		collector.add(server)
		collector.addParentRelationship(cfg.ParentIdentityKey, cfg.ParentRelation, server)
	}
	for _, hook := range extractHooks(doc, cfg) {
		collector.add(hook)
		collector.addParentRelationship(cfg.ParentIdentityKey, cfg.ParentRelation, hook)
	}
	if cfg.SourceType != sourceTypeMCPJSON {
		for _, skill := range extractSkills(doc, cfg) {
			collector.add(skill)
			collector.addParentRelationship(cfg.ParentIdentityKey, cfg.ParentRelation, skill)
		}
		for _, subagent := range extractSubagents(doc, cfg) {
			collector.add(subagent)
			collector.addParentRelationship(cfg.ParentIdentityKey, cfg.ParentRelation, subagent)
		}
	}

	if cfg.DiscoverProjects && projectRoots != nil {
		for _, root := range discoverProjectDirectories(doc, cfg) {
			key := root.ItemType + "|" + root.Path
			projectRoots[key] = root
		}
	}

	return nil
}

func (c *inventoryCollector) addSyntheticParseError(
	cfg jsonSurfaceConfig,
	surface parseErrorSurface,
	parseStatus string,
	err error,
) spmapi.SyncInventoryItem {
	identity := cfg.Path + "#parse_error#" + surface.ItemType
	item := spmapi.SyncInventoryItem{
		Harness:        claudeHarness,
		ItemType:       surface.ItemType,
		SourceType:     cfg.SourceType,
		SourceLocation: cfg.Path,
		IdentityKey:    identity,
		DisplayName:    filepath.Base(cfg.Path) + " " + surface.DisplaySuffix,
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
	}
	c.add(item)
	return item
}

func (c *inventoryCollector) addConfigItem(
	cfg jsonSurfaceConfig,
	itemType string,
	name string,
	value any,
) {
	contentHash, normalized := hashValue(value)
	identity := cfg.Path + "#" + itemType
	c.add(spmapi.SyncInventoryItem{
		Harness:        claudeHarness,
		ItemType:       itemType,
		SourceType:     cfg.SourceType,
		SourceLocation: cfg.Path,
		IdentityKey:    identity,
		DisplayName:    name + " in " + filepath.Base(cfg.Path),
		ContentHash:    contentHash,
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
	collector *inventoryCollector,
	projectRoot string,
	path string,
	sourceSurface string,
	sourceType string,
	enforceable bool,
) error {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		collector.add(spmapi.SyncInventoryItem{
			Harness:        claudeHarness,
			ItemType:       itemTypeInstructionFile,
			SourceType:     sourceType,
			SourceLocation: path,
			IdentityKey:    path,
			DisplayName:    filepath.Base(path),
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

	collector.add(spmapi.SyncInventoryItem{
		Harness:        claudeHarness,
		ItemType:       itemTypeInstructionFile,
		SourceType:     sourceType,
		SourceLocation: path,
		IdentityKey:    path,
		DisplayName:    filepath.Base(path),
		ContentHash:    hashBytes(data),
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

func collectComponentSurfaces(
	collector *inventoryCollector,
	claudeDir string,
	projectRoot string,
	sourcePrefix string,
	parentIdentityKey string,
) error {
	skillsRoot := filepath.Join(claudeDir, "skills")
	if err := filepath.WalkDir(skillsRoot, func(path string, entry os.DirEntry, err error) error {
		if err != nil || entry.IsDir() || entry.Name() != "SKILL.md" {
			return nil
		}
		name := filepath.Base(filepath.Dir(path))
		return collectMarkdownComponent(collector, markdownComponentConfig{
			Path:              path,
			ProjectRoot:       projectRoot,
			SourceSurface:     sourcePrefix + "_skill_frontmatter",
			ItemType:          itemTypeSkill,
			SourceType:        sourceTypeSkillFrontmatter,
			Name:              name,
			EvidenceKey:       "skill",
			ParentIdentityKey: parentIdentityKey,
		})
	}); err != nil && !os.IsNotExist(err) {
		return err
	}

	agentsRoot := filepath.Join(claudeDir, "agents")
	if err := filepath.WalkDir(agentsRoot, func(path string, entry os.DirEntry, err error) error {
		if err != nil || entry.IsDir() || filepath.Ext(entry.Name()) != ".md" {
			return nil
		}
		name := strings.TrimSuffix(entry.Name(), filepath.Ext(entry.Name()))
		return collectMarkdownComponent(collector, markdownComponentConfig{
			Path:              path,
			ProjectRoot:       projectRoot,
			SourceSurface:     sourcePrefix + "_subagent_frontmatter",
			ItemType:          itemTypeSubagent,
			SourceType:        sourceTypeSubagentFrontmatter,
			Name:              name,
			EvidenceKey:       "subagent",
			ParentIdentityKey: parentIdentityKey,
		})
	}); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func collectPluginSurfaces(collector *inventoryCollector, homeDir string) error {
	pluginsRoot := filepath.Join(homeDir, ".claude", "plugins")
	err := filepath.WalkDir(pluginsRoot, func(path string, entry os.DirEntry, err error) error {
		if err != nil || entry.IsDir() || entry.Name() != "plugin.json" {
			return nil
		}
		if filepath.Base(filepath.Dir(path)) != ".claude-plugin" {
			return nil
		}
		pluginRoot := filepath.Dir(filepath.Dir(path))
		manifest, err := collectPluginManifest(collector, pluginRoot, path)
		if err != nil {
			return err
		}
		pluginIdentityKey := manifest.IdentityKey
		if err := collectJSONSurface(collector, jsonSurfaceConfig{
			Path:              filepath.Join(pluginRoot, "hooks", "hooks.json"),
			SourceType:        sourceTypeHooksJSON,
			SourceSurface:     "plugin_hooks_json",
			ProjectRoot:       "",
			Writable:          false,
			ParentIdentityKey: pluginIdentityKey,
			ParentRelation:    relationshipTypeDefines,
			EmitParseOnError: []parseErrorSurface{
				{ItemType: itemTypeHook, DisplaySuffix: "hooks"},
			},
		}, nil); err != nil {
			return err
		}
		if err := collectJSONSurface(collector, jsonSurfaceConfig{
			Path:              filepath.Join(pluginRoot, ".mcp.json"),
			SourceType:        sourceTypeMCPJSON,
			SourceSurface:     "plugin_mcp_json",
			ProjectRoot:       "",
			Writable:          false,
			ParentIdentityKey: pluginIdentityKey,
			ParentRelation:    relationshipTypeDefines,
			EmitParseOnError: []parseErrorSurface{
				{ItemType: itemTypeMCPServer, DisplaySuffix: "mcp"},
			},
		}, nil); err != nil {
			return err
		}
		if err := collectPluginNamedJSONComponents(collector, pluginNamedJSONConfig{
			Path:              filepath.Join(pluginRoot, ".lsp.json"),
			PluginRoot:        pluginRoot,
			SourceType:        sourceTypeLSPJSON,
			SourceSurface:     "plugin_lsp_json",
			ItemType:          itemTypeLSPServer,
			DisplaySuffix:     "lsp",
			EntryKeys:         []string{"lspServers", "languageServers", "servers"},
			ParentIdentityKey: pluginIdentityKey,
		}); err != nil {
			return err
		}
		if err := collectPluginNamedJSONComponents(collector, pluginNamedJSONConfig{
			Path:              filepath.Join(pluginRoot, "monitors", "monitors.json"),
			PluginRoot:        pluginRoot,
			SourceType:        sourceTypeMonitorsJSON,
			SourceSurface:     "plugin_monitors_json",
			ItemType:          itemTypeMonitor,
			DisplaySuffix:     "monitors",
			EntryKeys:         []string{"monitors"},
			ParentIdentityKey: pluginIdentityKey,
		}); err != nil {
			return err
		}
		if err := collectPluginSettings(collector, pluginRoot, pluginIdentityKey); err != nil {
			return err
		}
		if err := collectPluginMarkdownComponents(collector, pluginRoot, pluginIdentityKey); err != nil {
			return err
		}
		if err := collectPluginBinaries(collector, pluginRoot, pluginIdentityKey); err != nil {
			return err
		}
		if err := collectPluginOutputStyles(collector, pluginRoot, manifest.Manifest, pluginIdentityKey); err != nil {
			return err
		}
		return collectPluginThemes(collector, pluginRoot, pluginIdentityKey)
	})
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

type markdownComponentConfig struct {
	Path              string
	ItemLocation      string
	ProjectRoot       string
	SourceSurface     string
	ItemType          string
	SourceType        string
	Name              string
	EvidenceKey       string
	ParentIdentityKey string
}

func collectMarkdownComponent(collector *inventoryCollector, cfg markdownComponentConfig) error {
	data, err := os.ReadFile(cfg.Path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	text := string(data)
	frontmatter := parseSimpleFrontmatter(text)
	if name, ok := stringValue(frontmatter["name"]); ok && name != "" {
		cfg.Name = name
	}
	if cfg.ItemLocation == "" {
		cfg.ItemLocation = cfg.Name
	}
	fingerprint := hashString(cfg.Path + "|" + text)
	component := map[string]any{
		"name":        cfg.Name,
		"frontmatter": frontmatter,
		"preview":     previewText(text),
	}
	item := spmapi.SyncInventoryItem{
		Harness:        claudeHarness,
		ItemType:       cfg.ItemType,
		SourceType:     cfg.SourceType,
		ItemLocation:   cfg.ItemLocation,
		SourceLocation: cfg.Path,
		IdentityKey:    cfg.Path,
		DisplayName:    cfg.Name,
		ContentHash:    hashBytes(data),
		Metadata: map[string]any{
			"file_path":      cfg.Path,
			"project_root":   cfg.ProjectRoot,
			"source_surface": cfg.SourceSurface,
			"parse_status":   parseStatusOK,
			"fingerprint":    fingerprint,
			"name":           cfg.Name,
		},
		Evidence: map[string]any{
			"file_path":       cfg.Path,
			"frontmatter":     frontmatter,
			cfg.EvidenceKey:   component,
			"content_preview": previewText(text),
		},
		ObservedState: map[string]any{
			"disabled": false,
		},
	}
	collector.add(item)
	collector.addParentRelationship(
		cfg.ParentIdentityKey,
		relationshipTypeDefines,
		item,
	)
	return nil
}

type pluginManifestResult struct {
	IdentityKey string
	Manifest    map[string]any
}

func collectPluginManifest(collector *inventoryCollector, pluginRoot string, manifestPath string) (pluginManifestResult, error) {
	data, err := os.ReadFile(manifestPath)
	if err != nil {
		if os.IsNotExist(err) {
			return pluginManifestResult{}, nil
		}
		return pluginManifestResult{}, err
	}
	var manifest map[string]any
	parseStatus := parseStatusOK
	if err := json.Unmarshal(data, &manifest); err != nil {
		parseStatus = parseStatusInvalid
		manifest = map[string]any{"parse_error": err.Error()}
	}
	name, _ := stringValue(manifest["name"])
	if name == "" {
		name = filepath.Base(pluginRoot)
	}
	version, _ := stringValue(manifest["version"])
	item := spmapi.SyncInventoryItem{
		Harness:        claudeHarness,
		ItemType:       itemTypePlugin,
		SourceType:     sourceTypePluginManifest,
		ItemLocation:   pluginRoot,
		SourceLocation: manifestPath,
		IdentityKey:    manifestPath,
		DisplayName:    name,
		ContentHash:    hashBytes(data),
		Metadata: map[string]any{
			"file_path":      manifestPath,
			"plugin_root":    pluginRoot,
			"source_surface": "plugin_manifest",
			"parse_status":   parseStatus,
			"name":           name,
			"version":        version,
		},
		Evidence: map[string]any{
			"file_path": manifestPath,
			"manifest":  manifest,
		},
		ObservedState: map[string]any{
			"enabled": true,
		},
	}
	collector.add(item)
	return pluginManifestResult{IdentityKey: item.IdentityKey, Manifest: manifest}, nil
}

type pluginNamedJSONConfig struct {
	Path              string
	PluginRoot        string
	SourceType        string
	SourceSurface     string
	ItemType          string
	DisplaySuffix     string
	EntryKeys         []string
	ParentIdentityKey string
}

func collectPluginNamedJSONComponents(collector *inventoryCollector, cfg pluginNamedJSONConfig) error {
	data, err := os.ReadFile(cfg.Path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		item := collector.addSyntheticParseError(cfg.jsonSurfaceConfig(), parseErrorSurface{
			ItemType:      cfg.ItemType,
			DisplaySuffix: cfg.DisplaySuffix,
		}, parseStatusUnreadable, err)
		collector.addParentRelationship(cfg.ParentIdentityKey, relationshipTypeDefines, item)
		return nil
	}

	var doc any
	if err := json.Unmarshal(data, &doc); err != nil {
		item := collector.addSyntheticParseError(cfg.jsonSurfaceConfig(), parseErrorSurface{
			ItemType:      cfg.ItemType,
			DisplaySuffix: cfg.DisplaySuffix,
		}, parseStatusInvalid, err)
		collector.addParentRelationship(cfg.ParentIdentityKey, relationshipTypeDefines, item)
		return nil
	}

	rawEntries := doc
	if object, ok := doc.(map[string]any); ok {
		for _, key := range cfg.EntryKeys {
			if raw, ok := object[key]; ok {
				rawEntries = raw
				break
			}
		}
	}

	for _, entry := range normalizeNamedEntries(rawEntries) {
		contentHash, normalized := hashValue(entry.Raw)
		item := spmapi.SyncInventoryItem{
			Harness:        claudeHarness,
			ItemType:       cfg.ItemType,
			SourceType:     cfg.SourceType,
			ItemLocation:   entry.Name,
			SourceLocation: cfg.Path,
			IdentityKey:    cfg.Path + "#" + cfg.ItemType + ":" + entry.Fingerprint,
			DisplayName:    entry.Name,
			ContentHash:    contentHash,
			Metadata: map[string]any{
				"file_path":      cfg.Path,
				"plugin_root":    cfg.PluginRoot,
				"source_surface": cfg.SourceSurface,
				"parse_status":   parseStatusOK,
				"fingerprint":    entry.Fingerprint,
				"name":           entry.Name,
			},
			Evidence: map[string]any{
				"file_path":      cfg.Path,
				"source_surface": cfg.SourceSurface,
				"config":         normalized,
			},
			ObservedState: map[string]any{
				"enabled": true,
			},
		}
		collector.add(item)
		collector.addParentRelationship(cfg.ParentIdentityKey, relationshipTypeDefines, item)
	}
	return nil
}

func (cfg pluginNamedJSONConfig) jsonSurfaceConfig() jsonSurfaceConfig {
	return jsonSurfaceConfig{
		Path:          cfg.Path,
		SourceType:    cfg.SourceType,
		SourceSurface: cfg.SourceSurface,
		ProjectRoot:   "",
		Writable:      false,
	}
}

func collectPluginSettings(collector *inventoryCollector, pluginRoot string, parentIdentityKey string) error {
	path := filepath.Join(pluginRoot, "settings.json")
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		item := collector.addPluginSettingsItem(pluginRoot, path, parseStatusUnreadable, nil, err)
		collector.addParentRelationship(parentIdentityKey, relationshipTypeDefines, item)
		return nil
	}

	var settings map[string]any
	parseStatus := parseStatusOK
	var parseErr error
	if err := json.Unmarshal(data, &settings); err != nil {
		parseStatus = parseStatusInvalid
		parseErr = err
		settings = map[string]any{"parse_error": err.Error()}
	}
	item := collector.addPluginSettingsItem(pluginRoot, path, parseStatus, settings, parseErr)
	collector.addParentRelationship(parentIdentityKey, relationshipTypeDefines, item)
	return nil
}

func (c *inventoryCollector) addPluginSettingsItem(
	pluginRoot string,
	path string,
	parseStatus string,
	settings map[string]any,
	err error,
) spmapi.SyncInventoryItem {
	if settings == nil {
		settings = map[string]any{}
	}
	contentHash, normalized := hashValue(settings)
	evidence := map[string]any{
		"file_path":      path,
		"source_surface": "plugin_settings_json",
		"settings":       normalized,
	}
	if err != nil {
		evidence["parse_error"] = err.Error()
		evidence["parse_status"] = parseStatus
	}
	item := spmapi.SyncInventoryItem{
		Harness:        claudeHarness,
		ItemType:       itemTypePluginSettings,
		SourceType:     sourceTypePluginSettingsJSON,
		ItemLocation:   pluginRelativePath(pluginRoot, path),
		SourceLocation: path,
		IdentityKey:    path + "#" + itemTypePluginSettings,
		DisplayName:    "settings.json",
		ContentHash:    contentHash,
		Metadata: map[string]any{
			"file_path":      path,
			"plugin_root":    pluginRoot,
			"source_surface": "plugin_settings_json",
			"parse_status":   parseStatus,
		},
		Evidence: evidence,
		ObservedState: map[string]any{
			"enabled": parseStatus == parseStatusOK,
			"value":   normalized,
		},
	}
	c.add(item)
	return item
}

func collectPluginMarkdownComponents(collector *inventoryCollector, pluginRoot string, parentIdentityKey string) error {
	componentSets := []struct {
		Root          string
		SourceSurface string
		ItemType      string
		SourceType    string
		EvidenceKey   string
	}{
		{Root: filepath.Join(pluginRoot, "skills"), SourceSurface: "plugin_skill_frontmatter", ItemType: itemTypeSkill, SourceType: sourceTypeSkillFrontmatter, EvidenceKey: "skill"},
		{Root: filepath.Join(pluginRoot, "agents"), SourceSurface: "plugin_subagent_frontmatter", ItemType: itemTypeSubagent, SourceType: sourceTypeSubagentFrontmatter, EvidenceKey: "subagent"},
		{Root: filepath.Join(pluginRoot, "commands"), SourceSurface: "plugin_command_file", ItemType: itemTypeCommand, SourceType: sourceTypeCommandFile, EvidenceKey: "command"},
	}
	for _, set := range componentSets {
		if err := filepath.WalkDir(set.Root, func(path string, entry os.DirEntry, err error) error {
			if err != nil || entry.IsDir() || filepath.Ext(entry.Name()) != ".md" {
				return nil
			}
			rel := pluginRelativePath(pluginRoot, path)
			name := defaultPluginMarkdownName(set.Root, path)
			return collectMarkdownComponent(collector, markdownComponentConfig{
				Path:              path,
				ItemLocation:      rel,
				ProjectRoot:       "",
				SourceSurface:     set.SourceSurface,
				ItemType:          set.ItemType,
				SourceType:        set.SourceType,
				Name:              name,
				EvidenceKey:       set.EvidenceKey,
				ParentIdentityKey: parentIdentityKey,
			})
		}); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	return nil
}

func collectPluginBinaries(collector *inventoryCollector, pluginRoot string, parentIdentityKey string) error {
	binRoot := filepath.Join(pluginRoot, "bin")
	entries, err := os.ReadDir(binRoot)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		if info.Mode()&0o111 == 0 {
			continue
		}
		path := filepath.Join(binRoot, entry.Name())
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		item := spmapi.SyncInventoryItem{
			Harness:        claudeHarness,
			ItemType:       itemTypeBinary,
			SourceType:     sourceTypeBinaryFile,
			ItemLocation:   path,
			SourceLocation: path,
			IdentityKey:    path,
			DisplayName:    entry.Name(),
			ContentHash:    hashBytes(data),
			Metadata: map[string]any{
				"file_path":      path,
				"plugin_root":    pluginRoot,
				"source_surface": "plugin_binary_file",
				"parse_status":   parseStatusOK,
				"mode":           info.Mode().String(),
				"size":           info.Size(),
			},
			Evidence: map[string]any{
				"file_path": path,
				"sha256":    hashBytes(data),
				"size":      info.Size(),
			},
			ObservedState: map[string]any{
				"enabled":    true,
				"executable": true,
			},
		}
		collector.add(item)
		collector.addParentRelationship(parentIdentityKey, relationshipTypeDefines, item)
	}
	return nil
}

func collectPluginOutputStyles(collector *inventoryCollector, pluginRoot string, manifest map[string]any, parentIdentityKey string) error {
	paths := map[string]struct{}{}
	outputStylesRoot := filepath.Join(pluginRoot, "output-styles")
	if err := filepath.WalkDir(outputStylesRoot, func(path string, entry os.DirEntry, err error) error {
		if err != nil || entry.IsDir() || filepath.Ext(entry.Name()) != ".md" {
			return nil
		}
		paths[path] = struct{}{}
		return nil
	}); err != nil && !os.IsNotExist(err) {
		return err
	}
	for _, manifestPath := range manifestOutputStylePaths(manifest) {
		path := manifestPath
		if !filepath.IsAbs(path) {
			path = filepath.Join(pluginRoot, path)
		}
		path = filepath.Clean(path)
		if filepath.Ext(path) == ".md" {
			paths[path] = struct{}{}
		}
	}

	sortedPaths := make([]string, 0, len(paths))
	for path := range paths {
		sortedPaths = append(sortedPaths, path)
	}
	sort.Strings(sortedPaths)
	for _, path := range sortedPaths {
		if _, err := os.Stat(path); err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return err
		}
		rel := pluginRelativePath(pluginRoot, path)
		if err := collectMarkdownComponent(collector, markdownComponentConfig{
			Path:              path,
			ItemLocation:      rel,
			ProjectRoot:       "",
			SourceSurface:     "plugin_output_style_file",
			ItemType:          itemTypeOutputStyle,
			SourceType:        sourceTypeOutputStyleFile,
			Name:              strings.TrimSuffix(filepath.Base(path), filepath.Ext(path)),
			EvidenceKey:       "output_style",
			ParentIdentityKey: parentIdentityKey,
		}); err != nil {
			return err
		}
	}
	return nil
}

func collectPluginThemes(collector *inventoryCollector, pluginRoot string, parentIdentityKey string) error {
	themesRoot := filepath.Join(pluginRoot, "themes")
	err := filepath.WalkDir(themesRoot, func(path string, entry os.DirEntry, err error) error {
		if err != nil || entry.IsDir() {
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return nil
		}
		rel := pluginRelativePath(pluginRoot, path)
		item := spmapi.SyncInventoryItem{
			Harness:        claudeHarness,
			ItemType:       itemTypeTheme,
			SourceType:     sourceTypeThemeFile,
			ItemLocation:   rel,
			SourceLocation: path,
			IdentityKey:    path,
			DisplayName:    strings.TrimSuffix(filepath.Base(path), filepath.Ext(path)),
			ContentHash:    hashBytes(data),
			Metadata: map[string]any{
				"file_path":      path,
				"plugin_root":    pluginRoot,
				"source_surface": "plugin_theme_file",
				"parse_status":   parseStatusOK,
			},
			Evidence: map[string]any{
				"file_path":       path,
				"content_preview": previewText(string(data)),
			},
			ObservedState: map[string]any{
				"enabled": true,
			},
		}
		collector.add(item)
		collector.addParentRelationship(parentIdentityKey, relationshipTypeDefines, item)
		return nil
	})
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func pluginRelativePath(pluginRoot string, path string) string {
	rel, err := filepath.Rel(pluginRoot, path)
	if err != nil {
		return path
	}
	return filepath.ToSlash(rel)
}

func defaultPluginMarkdownName(root string, path string) string {
	name := strings.TrimSuffix(filepath.Base(path), filepath.Ext(path))
	if name == "SKILL" {
		return filepath.Base(filepath.Dir(path))
	}
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return name
	}
	return strings.TrimSuffix(filepath.ToSlash(rel), filepath.Ext(rel))
}

func manifestOutputStylePaths(manifest map[string]any) []string {
	if manifest == nil {
		return nil
	}
	paths := []string{}
	for _, key := range []string{"outputStyles", "output_styles", "outputStyle", "output_style"} {
		if raw, ok := manifest[key]; ok {
			paths = appendManifestPaths(paths, raw)
		}
	}
	return uniqueStrings(paths)
}

func appendManifestPaths(paths []string, raw any) []string {
	switch value := raw.(type) {
	case string:
		if strings.TrimSpace(value) != "" {
			paths = append(paths, strings.TrimSpace(value))
		}
	case []any:
		for _, item := range value {
			paths = appendManifestPaths(paths, item)
		}
	case map[string]any:
		for _, key := range []string{"path", "file", "source"} {
			if rawPath, ok := value[key]; ok {
				paths = appendManifestPaths(paths, rawPath)
			}
		}
	}
	return paths
}

func parseSimpleFrontmatter(text string) map[string]any {
	if !strings.HasPrefix(text, "---\n") {
		return map[string]any{}
	}
	rest := strings.TrimPrefix(text, "---\n")
	end := strings.Index(rest, "\n---")
	if end < 0 {
		return map[string]any{}
	}
	lines := strings.Split(rest[:end], "\n")
	result := map[string]any{}
	for _, line := range lines {
		key, value, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		value = strings.Trim(strings.TrimSpace(value), `"'`)
		if key != "" && value != "" {
			result[key] = value
		}
	}
	return result
}

func discoverProjectDirectories(doc map[string]any, cfg jsonSurfaceConfig) []projectDirectory {
	dirs := map[string]projectDirectory{}
	for _, item := range readDirectoryEntries(doc, "trustedDirectories", "trusted_projects", "trustedProjectDirectories") {
		path := filepath.Clean(item)
		dirs[itemTypeTrustedDirectory+"|"+path] = projectDirectory{
			Path:          path,
			ItemType:      itemTypeTrustedDirectory,
			SourceSurface: cfg.SourceSurface,
			FilePath:      cfg.Path,
		}
	}
	for _, item := range readDirectoryEntries(doc, "additionalDirectories", "additional_projects", "additionalProjectDirectories") {
		path := filepath.Clean(item)
		dirs[itemTypeAdditionalDirectory+"|"+path] = projectDirectory{
			Path:          path,
			ItemType:      itemTypeAdditionalDirectory,
			SourceSurface: cfg.SourceSurface,
			FilePath:      cfg.Path,
		}
	}

	if rawProjects, ok := doc["projects"]; ok {
		if projects, ok := rawProjects.(map[string]any); ok {
			for projectPath, rawValue := range projects {
				path := filepath.Clean(projectPath)
				itemType := itemTypeTrustedDirectory
				if projectType := classifyProjectValue(rawValue); projectType != "" {
					itemType = projectType
				}
				dirs[itemType+"|"+path] = projectDirectory{
					Path:          path,
					ItemType:      itemType,
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
		if items[i].ItemType == items[j].ItemType {
			return items[i].Path < items[j].Path
		}
		return items[i].ItemType < items[j].ItemType
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
			return itemTypeAdditionalDirectory
		case "trusted":
			return itemTypeTrustedDirectory
		}
	}
	if trusted, ok := boolValue(project["trusted"]); ok && trusted {
		return itemTypeTrustedDirectory
	}
	if additional, ok := boolValue(project["additional"]); ok && additional {
		return itemTypeAdditionalDirectory
	}
	return ""
}

func extractMCPServers(doc map[string]any, cfg jsonSurfaceConfig) []spmapi.SyncInventoryItem {
	raw, ok := pickFirst(doc, "mcpServers", "mcp_servers")
	if !ok {
		return nil
	}

	items := []spmapi.SyncInventoryItem{}
	switch servers := raw.(type) {
	case map[string]any:
		for serverName, rawServer := range servers {
			if item, ok := buildMCPItem(cfg, serverName, rawServer); ok {
				items = append(items, item)
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
			if item, ok := buildMCPItem(cfg, serverName, rawServer); ok {
				items = append(items, item)
			}
		}
	}

	sort.Slice(items, func(i, j int) bool {
		return items[i].IdentityKey < items[j].IdentityKey
	})
	return items
}

func buildMCPItem(cfg jsonSurfaceConfig, serverName string, raw any) (spmapi.SyncInventoryItem, bool) {
	server, ok := raw.(map[string]any)
	if !ok {
		return spmapi.SyncInventoryItem{}, false
	}

	identity := claude.ResolveMCPIdentity(server)
	disabled, _ := boolValue(server["disabled"])
	contentHash, normalized := hashValue(server)
	identityKey := cfg.Path + "#mcp:" + serverName + "|" + identity.Resolved

	metadata := map[string]any{
		"file_path":         cfg.Path,
		"project_root":      cfg.ProjectRoot,
		"source_surface":    cfg.SourceSurface,
		"parse_status":      parseStatusOK,
		"writable":          cfg.Writable,
		"server_name":       serverName,
		"resolved_identity": identity.Resolved,
		"transport":         identity.Transport,
		"mcp_identity_key":  serverName + "|" + identity.Resolved,
		"approval_identity": map[string]any{"server_name": serverName, "resolved_identity": identity.Resolved},
	}
	for key, value := range identity.Evidence {
		metadata[key] = value
	}

	evidence := map[string]any{
		"file_path":         cfg.Path,
		"server_name":       serverName,
		"resolved_identity": identity.Resolved,
		"transport":         identity.Transport,
		"config":            normalized,
	}
	for key, value := range indicatorsFromServer(server) {
		evidence[key] = value
	}

	return spmapi.SyncInventoryItem{
		Harness:        claudeHarness,
		ItemType:       itemTypeMCPServer,
		SourceType:     cfg.SourceType,
		ItemLocation:   serverName + "|" + identity.Resolved,
		SourceLocation: cfg.Path,
		IdentityKey:    identityKey,
		DisplayName:    serverName,
		ContentHash:    contentHash,
		Metadata:       metadata,
		Evidence:       evidence,
		ObservedState: map[string]any{
			"disabled": disabled,
		},
	}, true
}

func extractHooks(doc map[string]any, cfg jsonSurfaceConfig) []spmapi.SyncInventoryItem {
	raw, ok := doc["hooks"]
	if !ok {
		if cfg.SourceType != sourceTypeHooksJSON {
			return nil
		}
		raw = doc
	}
	items := []spmapi.SyncInventoryItem{}
	hooks, ok := raw.(map[string]any)
	if !ok {
		return nil
	}
	for eventName, eventValue := range hooks {
		for index, hook := range normalizeHookEntries(eventValue) {
			fingerprint := fmt.Sprintf("%s|%s|%s|%d", eventName, hook.Matcher, hook.Command, index)
			contentHash, normalized := hashValue(hook.Raw)
			items = append(items, spmapi.SyncInventoryItem{
				Harness:        claudeHarness,
				ItemType:       itemTypeHook,
				SourceType:     cfg.SourceType,
				SourceLocation: cfg.Path,
				IdentityKey:    cfg.Path + "#hook:" + fingerprint,
				DisplayName:    hookDisplayName(eventName, hook.Matcher, hook.Command),
				ContentHash:    contentHash,
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
	sort.Slice(items, func(i, j int) bool {
		return items[i].IdentityKey < items[j].IdentityKey
	})
	return items
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

func extractSkills(doc map[string]any, cfg jsonSurfaceConfig) []spmapi.SyncInventoryItem {
	raw, ok := doc["skills"]
	if !ok {
		return nil
	}
	items := []spmapi.SyncInventoryItem{}
	for _, skill := range normalizeNamedEntries(raw) {
		contentHash, normalized := hashValue(skill.Raw)
		items = append(items, spmapi.SyncInventoryItem{
			Harness:        claudeHarness,
			ItemType:       itemTypeSkill,
			SourceType:     cfg.SourceType,
			SourceLocation: cfg.Path,
			IdentityKey:    cfg.Path + "#skill:" + skill.Fingerprint,
			DisplayName:    skill.Name,
			ContentHash:    contentHash,
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
	sort.Slice(items, func(i, j int) bool {
		return items[i].IdentityKey < items[j].IdentityKey
	})
	return items
}

func extractSubagents(doc map[string]any, cfg jsonSurfaceConfig) []spmapi.SyncInventoryItem {
	raw, ok := doc["subagents"]
	if !ok {
		return nil
	}
	items := []spmapi.SyncInventoryItem{}
	for _, item := range normalizeNamedEntries(raw) {
		contentHash, normalized := hashValue(item.Raw)
		items = append(items, spmapi.SyncInventoryItem{
			Harness:        claudeHarness,
			ItemType:       itemTypeSubagent,
			SourceType:     cfg.SourceType,
			SourceLocation: cfg.Path,
			IdentityKey:    cfg.Path + "#subagent:" + item.Fingerprint,
			DisplayName:    item.Name,
			ContentHash:    contentHash,
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
	sort.Slice(items, func(i, j int) bool {
		return items[i].IdentityKey < items[j].IdentityKey
	})
	return items
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
