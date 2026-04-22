package tasks

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"time"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/claude"
	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/spmapi"
)

// Executor applies desired-state tasks returned by the SPM sync API.
type Executor interface {
	Execute(context.Context, []spmapi.EnforcementTask) ([]spmapi.SyncTaskResult, error)
}

// ClaudeExecutor reconciles writable Claude Code settings.
type ClaudeExecutor struct {
	HomeDir string
}

type writableSurface string

const (
	writableSurfaceUserSettings writableSurface = "user_settings"
	writableSurfaceUserState    writableSurface = "user_state"
	writableSurfaceProjectLocal writableSurface = "project_local_settings"
)

func NewClaudeExecutor(homeDir string) ClaudeExecutor {
	return ClaudeExecutor{HomeDir: strings.TrimSpace(homeDir)}
}

func (e ClaudeExecutor) userSettingsPath() string {
	return filepath.Clean(filepath.Join(e.HomeDir, ".claude", "settings.json"))
}

func (e ClaudeExecutor) userStatePath() string {
	return filepath.Clean(filepath.Join(e.HomeDir, ".claude.json"))
}

func (e ClaudeExecutor) resolveTargetPath(
	requestedPath string,
	defaultPath string,
	allowed ...writableSurface,
) (string, writableSurface, error) {
	targetPath := strings.TrimSpace(requestedPath)
	if targetPath == "" {
		targetPath = strings.TrimSpace(defaultPath)
	}
	if targetPath == "" {
		return "", "", fmt.Errorf("target path is required")
	}

	cleanPath := filepath.Clean(targetPath)
	surface, ok := e.classifyWritableSurface(cleanPath)
	if !ok {
		return "", "", fmt.Errorf("target path %q is not a writable Claude config surface", cleanPath)
	}

	for _, allowedSurface := range allowed {
		if surface == allowedSurface {
			return cleanPath, surface, nil
		}
	}
	return "", "", fmt.Errorf("target path %q is not allowed for this action", cleanPath)
}

func (e ClaudeExecutor) classifyWritableSurface(path string) (writableSurface, bool) {
	cleanPath := filepath.Clean(strings.TrimSpace(path))
	switch {
	case cleanPath == e.userSettingsPath():
		return writableSurfaceUserSettings, true
	case cleanPath == e.userStatePath():
		return writableSurfaceUserState, true
	case filepath.Base(cleanPath) == "settings.local.json" &&
		filepath.Base(filepath.Dir(cleanPath)) == ".claude":
		return writableSurfaceProjectLocal, true
	default:
		return "", false
	}
}

func (e ClaudeExecutor) Execute(
	ctx context.Context,
	enforcementTasks []spmapi.EnforcementTask,
) ([]spmapi.SyncTaskResult, error) {
	results := make([]spmapi.SyncTaskResult, 0, len(enforcementTasks))
	for _, task := range enforcementTasks {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		results = append(results, e.executeTask(task))
	}
	return results, nil
}

func (e ClaudeExecutor) executeTask(task spmapi.EnforcementTask) spmapi.SyncTaskResult {
	completedAt := time.Now().UTC()
	result := spmapi.SyncTaskResult{
		TaskID:      task.ID,
		CompletedAt: completedAt,
	}

	status, details, err := e.apply(task)
	if err != nil {
		result.Status = spmapi.TaskResultStatusFailed
		result.Error = err.Error()
		return result
	}
	result.Status = status
	result.Result = details
	return result
}

func (e ClaudeExecutor) apply(
	task spmapi.EnforcementTask,
) (spmapi.TaskResultStatus, map[string]any, error) {
	switch task.Action {
	case "disable_mcp_server":
		return e.disableMCPServer(task.Payload)
	case "exclude_instruction_file":
		return e.excludeInstructionFile(task.Payload)
	case "revoke_trusted_directory":
		return e.revokeDirectory(task.Payload, "trustedDirectories", "trusted")
	case "revoke_additional_directory":
		return e.revokeDirectory(task.Payload, "additionalDirectories", "additional")
	case "reconcile_permission_config":
		return e.reconcileConfig(task.Payload, "permissions")
	case "reconcile_sandbox_config":
		return e.reconcileConfig(task.Payload, "sandbox")
	case "disable_hook":
		return e.disableHook(task.Payload)
	case "disable_skill":
		return e.disableSkill(task.Payload)
	default:
		return spmapi.TaskResultStatusFailed, nil, fmt.Errorf("unsupported enforcement action %q", task.Action)
	}
}

func (e ClaudeExecutor) disableMCPServer(payload map[string]any) (spmapi.TaskResultStatus, map[string]any, error) {
	serverName, err := requireString(payload, "server_name")
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	resolvedIdentity, _ := optionalString(payload, "resolved_identity")
	sourcePath, _ := optionalString(payload, "source_path")
	projectRoot, _ := optionalString(payload, "project_root")
	targetPath, _ := optionalString(payload, "target_path")

	if targetPath == "" {
		if strings.HasSuffix(sourcePath, string(filepath.Separator)+".mcp.json") || sourcePath == ".mcp.json" || strings.Contains(sourcePath, "/.mcp.json") || projectRoot != "" {
			if projectRoot == "" && sourcePath != "" {
				projectRoot = filepath.Dir(sourcePath)
			}
			targetPath = filepath.Join(projectRoot, ".claude", "settings.local.json")
		} else {
			targetPath = e.userStatePath()
		}
	}

	targetPath, surface, err := e.resolveTargetPath(targetPath, "", writableSurfaceUserState, writableSurfaceProjectLocal)
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}

	doc, err := loadJSONDocument(targetPath)
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}

	if surface == writableSurfaceProjectLocal {
		list := uniqueStrings(stringSlice(doc["disabledMcpjsonServers"]))
		if containsString(list, serverName) {
			return spmapi.TaskResultStatusSkipped, map[string]any{
				"reason":        "project mcp server already disabled",
				"target_path":   targetPath,
				"server_name":   serverName,
				"applied_value": list,
			}, nil
		}
		list = append(list, serverName)
		sort.Strings(list)
		doc["disabledMcpjsonServers"] = list
		if err := writeJSONDocument(targetPath, doc); err != nil {
			return spmapi.TaskResultStatusFailed, nil, err
		}
		return spmapi.TaskResultStatusApplied, map[string]any{
			"target_path":   targetPath,
			"server_name":   serverName,
			"applied_value": list,
		}, nil
	}

	changed, alreadyDisabled := disableMCPEntry(doc, serverName, resolvedIdentity)
	if !changed {
		reason := "mcp server entry not present"
		if alreadyDisabled {
			reason = "mcp server already disabled"
		}
		return spmapi.TaskResultStatusSkipped, map[string]any{
			"reason":            reason,
			"target_path":       targetPath,
			"server_name":       serverName,
			"resolved_identity": resolvedIdentity,
		}, nil
	}
	if err := writeJSONDocument(targetPath, doc); err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	return spmapi.TaskResultStatusApplied, map[string]any{
		"target_path":       targetPath,
		"server_name":       serverName,
		"resolved_identity": resolvedIdentity,
	}, nil
}

func disableMCPEntry(doc map[string]any, serverName string, resolvedIdentity string) (bool, bool) {
	rawServers, ok := doc["mcpServers"]
	if !ok {
		return false, false
	}

	changed := false
	alreadyDisabled := false

	switch servers := rawServers.(type) {
	case map[string]any:
		for name, rawServer := range servers {
			if name != serverName {
				continue
			}
			serverMap, ok := rawServer.(map[string]any)
			if !ok {
				continue
			}
			if resolvedIdentity != "" && claude.ResolveMCPIdentity(serverMap).Resolved != resolvedIdentity {
				continue
			}
			if disabled, ok := serverMap["disabled"].(bool); ok && disabled {
				alreadyDisabled = true
				continue
			}
			serverMap["disabled"] = true
			servers[name] = serverMap
			changed = true
		}
		doc["mcpServers"] = servers
	case []any:
		for index, rawServer := range servers {
			serverMap, ok := rawServer.(map[string]any)
			if !ok {
				continue
			}
			name, _ := pickString(serverMap, "name", "serverName")
			if name != serverName {
				continue
			}
			if resolvedIdentity != "" && claude.ResolveMCPIdentity(serverMap).Resolved != resolvedIdentity {
				continue
			}
			if disabled, ok := serverMap["disabled"].(bool); ok && disabled {
				alreadyDisabled = true
				continue
			}
			serverMap["disabled"] = true
			servers[index] = serverMap
			changed = true
		}
		doc["mcpServers"] = servers
	}
	return changed, alreadyDisabled
}

func (e ClaudeExecutor) excludeInstructionFile(payload map[string]any) (spmapi.TaskResultStatus, map[string]any, error) {
	filePath, err := requireString(payload, "file_path")
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	targetPath, _ := optionalString(payload, "target_path")
	projectRoot, _ := optionalString(payload, "project_root")
	if targetPath == "" {
		if projectRoot != "" {
			targetPath = filepath.Join(projectRoot, ".claude", "settings.local.json")
		} else {
			targetPath = e.userSettingsPath()
		}
	}
	targetPath, _, err = e.resolveTargetPath(targetPath, "", writableSurfaceUserSettings, writableSurfaceProjectLocal)
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	doc, err := loadJSONDocument(targetPath)
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}

	list := uniqueStrings(stringSlice(doc["claudeMdExcludes"]))
	if containsString(list, filePath) {
		return spmapi.TaskResultStatusSkipped, map[string]any{
			"reason":        "instruction file already excluded",
			"target_path":   targetPath,
			"excluded_path": filePath,
		}, nil
	}
	list = append(list, filePath)
	sort.Strings(list)
	doc["claudeMdExcludes"] = list
	if err := writeJSONDocument(targetPath, doc); err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	return spmapi.TaskResultStatusApplied, map[string]any{
		"target_path":   targetPath,
		"excluded_path": filePath,
	}, nil
}

func (e ClaudeExecutor) revokeDirectory(
	payload map[string]any,
	arrayKey string,
	projectField string,
) (spmapi.TaskResultStatus, map[string]any, error) {
	pathValue, err := requireString(payload, "directory_path")
	if err != nil {
		pathValue, err = requireString(payload, "path")
		if err != nil {
			return spmapi.TaskResultStatusFailed, nil, fmt.Errorf("directory path is required")
		}
	}
	targetPath, _ := optionalString(payload, "target_path")
	if targetPath == "" {
		targetPath = e.userStatePath()
	}
	targetPath, _, err = e.resolveTargetPath(targetPath, "", writableSurfaceUserState)
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	doc, err := loadJSONDocument(targetPath)
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}

	changed := false
	list := removeString(stringSlice(doc[arrayKey]), pathValue)
	if len(list) != len(stringSlice(doc[arrayKey])) {
		doc[arrayKey] = list
		changed = true
	}

	if projects, ok := doc["projects"].(map[string]any); ok {
		if rawProject, ok := projects[pathValue]; ok {
			switch project := rawProject.(type) {
			case map[string]any:
				if projectField == "trusted" {
					if _, ok := project["trusted"]; ok {
						delete(project, "trusted")
						changed = true
					}
					if trustLevel, ok := project["trustLevel"].(string); ok && strings.EqualFold(trustLevel, "trusted") {
						delete(project, "trustLevel")
						changed = true
					}
				}
				if projectField == "additional" {
					if _, ok := project["additional"]; ok {
						delete(project, "additional")
						changed = true
					}
					if trustLevel, ok := project["trustLevel"].(string); ok && strings.EqualFold(trustLevel, "additional") {
						delete(project, "trustLevel")
						changed = true
					}
				}
				if len(project) == 0 {
					delete(projects, pathValue)
				} else {
					projects[pathValue] = project
				}
			default:
				delete(projects, pathValue)
				changed = true
			}
			doc["projects"] = projects
		}
	}

	if !changed {
		return spmapi.TaskResultStatusSkipped, map[string]any{
			"reason":         "directory already revoked",
			"target_path":    targetPath,
			"directory_path": pathValue,
		}, nil
	}
	if err := writeJSONDocument(targetPath, doc); err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	return spmapi.TaskResultStatusApplied, map[string]any{
		"target_path":    targetPath,
		"directory_path": pathValue,
	}, nil
}

func (e ClaudeExecutor) reconcileConfig(
	payload map[string]any,
	key string,
) (spmapi.TaskResultStatus, map[string]any, error) {
	targetPath, _ := optionalString(payload, "target_path")
	if targetPath == "" {
		targetPath = e.userSettingsPath()
	}
	targetPath, _, err := e.resolveTargetPath(targetPath, "", writableSurfaceUserSettings, writableSurfaceUserState, writableSurfaceProjectLocal)
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	value, ok := pickValue(payload, "value", "approved_value", "config")
	if !ok {
		return spmapi.TaskResultStatusFailed, nil, fmt.Errorf("%s value is required", key)
	}
	doc, err := loadJSONDocument(targetPath)
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	if reflect.DeepEqual(doc[key], value) {
		return spmapi.TaskResultStatusSkipped, map[string]any{
			"reason":      key + " already reconciled",
			"target_path": targetPath,
		}, nil
	}
	doc[key] = value
	if err := writeJSONDocument(targetPath, doc); err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	return spmapi.TaskResultStatusApplied, map[string]any{
		"target_path": targetPath,
		"key":         key,
	}, nil
}

func (e ClaudeExecutor) disableHook(
	payload map[string]any,
) (spmapi.TaskResultStatus, map[string]any, error) {
	fingerprint, err := requireString(payload, "fingerprint")
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, fmt.Errorf("hooks identifier is required")
	}
	targetPath, _ := optionalString(payload, "target_path")
	if targetPath == "" {
		targetPath = e.userStatePath()
	}
	targetPath, _, err = e.resolveTargetPath(targetPath, "", writableSurfaceUserSettings, writableSurfaceUserState, writableSurfaceProjectLocal)
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}

	doc, err := loadJSONDocument(targetPath)
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}

	hooks, ok := doc["hooks"].(map[string]any)
	if !ok {
		return spmapi.TaskResultStatusSkipped, map[string]any{
			"reason":      "hooks entry already disabled",
			"target_path": targetPath,
			"fingerprint": fingerprint,
		}, nil
	}

	changed := false
	for eventName, eventValue := range hooks {
		index := 0
		filtered, removed := filterHookEntries(eventValue, eventName, fingerprint, &index)
		if !removed {
			continue
		}
		changed = true
		if filtered == nil {
			delete(hooks, eventName)
			continue
		}
		hooks[eventName] = filtered
	}

	if !changed {
		return spmapi.TaskResultStatusSkipped, map[string]any{
			"reason":      "hooks entry already disabled",
			"target_path": targetPath,
			"fingerprint": fingerprint,
		}, nil
	}

	doc["hooks"] = hooks
	if err := writeJSONDocument(targetPath, doc); err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	return spmapi.TaskResultStatusApplied, map[string]any{
		"target_path": targetPath,
		"fingerprint": fingerprint,
	}, nil
}

func (e ClaudeExecutor) disableSkill(
	payload map[string]any,
) (spmapi.TaskResultStatus, map[string]any, error) {
	return e.disableNamedEntry(payload, "skills", writableSurfaceUserSettings, writableSurfaceUserState, writableSurfaceProjectLocal)
}

func (e ClaudeExecutor) disableNamedEntry(
	payload map[string]any,
	key string,
	allowed ...writableSurface,
) (spmapi.TaskResultStatus, map[string]any, error) {
	fingerprint, err := requireString(payload, "fingerprint")
	if err != nil {
		fingerprint, err = requireString(payload, "name")
		if err != nil {
			return spmapi.TaskResultStatusFailed, nil, fmt.Errorf("%s identifier is required", key)
		}
	}
	targetPath, _ := optionalString(payload, "target_path")
	if targetPath == "" {
		targetPath = e.userStatePath()
	}
	targetPath, _, err = e.resolveTargetPath(targetPath, "", allowed...)
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	doc, err := loadJSONDocument(targetPath)
	if err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}

	changed := false
	switch entries := doc[key].(type) {
	case []any:
		next := make([]any, 0, len(entries))
		for _, entry := range entries {
			if namedEntryFingerprint(entry) == fingerprint {
				changed = true
				continue
			}
			next = append(next, entry)
		}
		doc[key] = next
	case map[string]any:
		for name, entry := range entries {
			if namedEntryFingerprintWithName(name, entry) == fingerprint || name == fingerprint {
				delete(entries, name)
				changed = true
			}
		}
		doc[key] = entries
	}

	if !changed {
		return spmapi.TaskResultStatusSkipped, map[string]any{
			"reason":      key + " entry already disabled",
			"target_path": targetPath,
			"fingerprint": fingerprint,
		}, nil
	}
	if err := writeJSONDocument(targetPath, doc); err != nil {
		return spmapi.TaskResultStatusFailed, nil, err
	}
	return spmapi.TaskResultStatusApplied, map[string]any{
		"target_path": targetPath,
		"fingerprint": fingerprint,
	}, nil
}

func namedEntryFingerprint(raw any) string {
	switch value := raw.(type) {
	case string:
		return value
	case map[string]any:
		name, _ := pickString(value, "name", "path", "id")
		if name == "" {
			return mustJSON(value)
		}
		return hashString(name + "|" + mustJSON(value))
	default:
		return fmt.Sprintf("%v", raw)
	}
}

func namedEntryFingerprintWithName(name string, raw any) string {
	return hashString(name + "|" + mustJSON(raw))
}

func filterHookEntries(raw any, eventName string, fingerprint string, index *int) (any, bool) {
	switch value := raw.(type) {
	case []any:
		next := make([]any, 0, len(value))
		removed := false
		for _, item := range value {
			filtered, itemRemoved := filterHookEntries(item, eventName, fingerprint, index)
			if itemRemoved {
				removed = true
			}
			if filtered != nil {
				next = append(next, filtered)
			}
		}
		if len(next) == 0 {
			return nil, removed
		}
		return next, removed
	case map[string]any:
		hook := normalizeHookEntry(value)
		currentFingerprint := fmt.Sprintf("%s|%s|%s|%d", eventName, hook.Matcher, hook.Command, *index)
		*index++
		if currentFingerprint == fingerprint {
			return nil, true
		}
		return value, false
	default:
		return raw, false
	}
}

type hookFingerprint struct {
	Matcher string
	Command string
}

func normalizeHookEntry(value map[string]any) hookFingerprint {
	matcher, _ := pickString(value, "matcher", "name")
	command, _ := pickString(value, "command", "cmd")
	if command == "" {
		if commands, ok := value["commands"].([]any); ok {
			parts := make([]string, 0, len(commands))
			for _, commandPart := range commands {
				text, ok := commandPart.(string)
				if ok && strings.TrimSpace(text) != "" {
					parts = append(parts, strings.TrimSpace(text))
				}
			}
			command = strings.Join(parts, " ")
		}
	}
	return hookFingerprint{Matcher: matcher, Command: command}
}

func loadJSONDocument(path string) (map[string]any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return map[string]any{}, nil
		}
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	var doc map[string]any
	if err := json.Unmarshal(data, &doc); err != nil {
		return nil, fmt.Errorf("decode %s: %w", path, err)
	}
	return doc, nil
}

func writeJSONDocument(path string, doc map[string]any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("create config directory for %s: %w", path, err)
	}
	data, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return fmt.Errorf("encode %s: %w", path, err)
	}
	data = append(data, '\n')
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	return nil
}

func requireString(payload map[string]any, key string) (string, error) {
	value, ok := optionalString(payload, key)
	if !ok {
		return "", fmt.Errorf("payload %q is required", key)
	}
	return value, nil
}

func optionalString(payload map[string]any, key string) (string, bool) {
	raw, ok := payload[key]
	if !ok {
		return "", false
	}
	value, ok := raw.(string)
	if !ok || strings.TrimSpace(value) == "" {
		return "", false
	}
	return strings.TrimSpace(value), true
}

func pickValue(payload map[string]any, keys ...string) (any, bool) {
	for _, key := range keys {
		if value, ok := payload[key]; ok {
			return value, true
		}
	}
	return nil, false
}

func stringSlice(raw any) []string {
	switch value := raw.(type) {
	case []string:
		return value
	case []any:
		items := make([]string, 0, len(value))
		for _, item := range value {
			text, ok := item.(string)
			if ok && strings.TrimSpace(text) != "" {
				items = append(items, strings.TrimSpace(text))
			}
		}
		return items
	default:
		return nil
	}
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
	return result
}

func containsString(items []string, target string) bool {
	for _, item := range items {
		if item == target {
			return true
		}
	}
	return false
}

func removeString(items []string, target string) []string {
	filtered := make([]string, 0, len(items))
	for _, item := range items {
		if item == target {
			continue
		}
		filtered = append(filtered, item)
	}
	return filtered
}

func pickString(doc map[string]any, keys ...string) (string, bool) {
	for _, key := range keys {
		if raw, ok := doc[key]; ok {
			if value, ok := raw.(string); ok && strings.TrimSpace(value) != "" {
				return strings.TrimSpace(value), true
			}
		}
	}
	return "", false
}

func mustJSON(value any) string {
	data, err := json.Marshal(value)
	if err != nil {
		return fmt.Sprintf("%v", value)
	}
	return string(data)
}

func hashString(value string) string {
	return value
}
