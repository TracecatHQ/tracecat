package tasks

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/spmapi"
)

func TestClaudeExecutorDisablesUserMCPByResolvedIdentity(t *testing.T) {
	t.Parallel()

	homeDir := filepath.Join(t.TempDir(), "home")
	statePath := filepath.Join(homeDir, ".claude.json")
	writeFile(t, statePath, `{
  "mcpServers": [
    {"name":"github","url":"HTTPS://api.github.com/mcp/?foo=1#frag"},
    {"name":"github","url":"https://allowed.example/mcp"},
    {"name":"tracecat","command":"uvx","args":["@tracecat/mcp","--stdio"]}
  ]
}
`)

	executor := NewClaudeExecutor(homeDir)
	results := executeTasks(t, executor, []spmapi.EnforcementTask{
		{
			ID:     "task-http",
			Action: "disable_mcp_server",
			Payload: map[string]any{
				"server_name":       "github",
				"resolved_identity": "https://api.github.com/mcp",
			},
		},
		{
			ID:     "task-stdio",
			Action: "disable_mcp_server",
			Payload: map[string]any{
				"server_name":       "tracecat",
				"resolved_identity": "package:@tracecat/mcp",
			},
		},
	})

	if results[0].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected http status %q", results[0].Status)
	}
	if results[1].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected stdio status %q", results[1].Status)
	}

	doc := readJSONDocument(t, statePath)
	servers := doc["mcpServers"].([]any)
	httpTarget := servers[0].(map[string]any)
	httpUntouched := servers[1].(map[string]any)
	stdioTarget := servers[2].(map[string]any)

	if disabled, _ := httpTarget["disabled"].(bool); !disabled {
		t.Fatal("expected targeted http MCP server to be disabled")
	}
	if _, ok := httpUntouched["disabled"]; ok {
		t.Fatal("expected non-matching http MCP server to remain enabled")
	}
	if disabled, _ := stdioTarget["disabled"].(bool); !disabled {
		t.Fatal("expected targeted stdio MCP server to be disabled")
	}

	retryResults := executeTasks(t, executor, []spmapi.EnforcementTask{
		{
			ID:     "task-http",
			Action: "disable_mcp_server",
			Payload: map[string]any{
				"server_name":       "github",
				"resolved_identity": "https://api.github.com/mcp",
			},
		},
		{
			ID:     "task-stdio",
			Action: "disable_mcp_server",
			Payload: map[string]any{
				"server_name":       "tracecat",
				"resolved_identity": "package:@tracecat/mcp",
			},
		},
	})

	if retryResults[0].Status != spmapi.TaskResultStatusSkipped {
		t.Fatalf("expected http retry to skip, got %q", retryResults[0].Status)
	}
	if retryResults[1].Status != spmapi.TaskResultStatusSkipped {
		t.Fatalf("expected stdio retry to skip, got %q", retryResults[1].Status)
	}
}

func TestClaudeExecutorDisablesProjectMCPViaDisabledMcpjsonServers(t *testing.T) {
	t.Parallel()

	homeDir := copyInventoryFixture(t, "claude")
	projectRoot := filepath.Join(homeDir, "workspace-alpha")
	projectMCPPath := filepath.Join(projectRoot, ".mcp.json")

	executor := NewClaudeExecutor(homeDir)
	task := spmapi.EnforcementTask{
		ID:     "task-project-mcp",
		Action: "disable_mcp_server",
		Payload: map[string]any{
			"server_name":  "slack",
			"project_root": projectRoot,
			"source_path":  projectMCPPath,
		},
	}

	results := executeTasks(t, executor, []spmapi.EnforcementTask{task})
	if results[0].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected status %q", results[0].Status)
	}

	localSettingsPath := filepath.Join(projectRoot, ".claude", "settings.local.json")
	doc := readJSONDocument(t, localSettingsPath)
	disabled := stringSlice(doc["disabledMcpjsonServers"])
	if len(disabled) != 1 || disabled[0] != "slack" {
		t.Fatalf("unexpected disabledMcpjsonServers %v", disabled)
	}

	originalMCP := readJSONDocument(t, projectMCPPath)
	if _, ok := originalMCP["mcpServers"]; !ok {
		t.Fatal("expected .mcp.json to remain untouched")
	}

	retryResults := executeTasks(t, executor, []spmapi.EnforcementTask{task})
	if retryResults[0].Status != spmapi.TaskResultStatusSkipped {
		t.Fatalf("expected idempotent skip, got %q", retryResults[0].Status)
	}
}

func TestClaudeExecutorExcludesInstructionFileByPathWithoutRewritingSource(t *testing.T) {
	t.Parallel()

	homeDir := copyInventoryFixture(t, "claude")
	projectRoot := filepath.Join(homeDir, "workspace-alpha")
	instructionPath := filepath.Join(projectRoot, "CLAUDE.md")
	originalContent := readFile(t, instructionPath)

	executor := NewClaudeExecutor(homeDir)
	task := spmapi.EnforcementTask{
		ID:     "task-instruction",
		Action: "exclude_instruction_file",
		Payload: map[string]any{
			"file_path":    instructionPath,
			"project_root": projectRoot,
		},
	}

	results := executeTasks(t, executor, []spmapi.EnforcementTask{task})
	if results[0].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected status %q", results[0].Status)
	}

	localSettingsPath := filepath.Join(projectRoot, ".claude", "settings.local.json")
	doc := readJSONDocument(t, localSettingsPath)
	excludes := stringSlice(doc["claudeMdExcludes"])
	if len(excludes) != 1 || excludes[0] != instructionPath {
		t.Fatalf("unexpected claudeMdExcludes %v", excludes)
	}
	if got := readFile(t, instructionPath); got != originalContent {
		t.Fatalf("expected CLAUDE.md to remain unchanged, got %q", got)
	}

	retryResults := executeTasks(t, executor, []spmapi.EnforcementTask{task})
	if retryResults[0].Status != spmapi.TaskResultStatusSkipped {
		t.Fatalf("expected idempotent skip, got %q", retryResults[0].Status)
	}
}

func TestClaudeExecutorManagedShadowedWritesStayInProjectLocalSettings(t *testing.T) {
	t.Parallel()

	homeDir := copyInventoryFixture(t, "claude-managed-shadowed")
	projectRoot := filepath.Join(homeDir, "workspace-shadowed")
	projectSettingsPath := filepath.Join(projectRoot, ".claude", "settings.json")
	projectLocalPath := filepath.Join(projectRoot, ".claude", "settings.local.json")
	projectMCPPath := filepath.Join(projectRoot, ".mcp.json")
	instructionPath := filepath.Join(projectRoot, "CLAUDE.md")

	managedBefore := readFile(t, projectSettingsPath)

	executor := NewClaudeExecutor(homeDir)
	results := executeTasks(t, executor, []spmapi.EnforcementTask{
		{
			ID:     "task-shadowed-mcp",
			Action: "disable_mcp_server",
			Payload: map[string]any{
				"server_name":  "shadowed-server",
				"project_root": projectRoot,
				"source_path":  projectMCPPath,
			},
		},
		{
			ID:     "task-shadowed-instruction",
			Action: "exclude_instruction_file",
			Payload: map[string]any{
				"file_path":    instructionPath,
				"project_root": projectRoot,
			},
		},
	})

	if results[0].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected managed-shadowed mcp status %q", results[0].Status)
	}
	if results[1].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected managed-shadowed instruction status %q", results[1].Status)
	}

	localDoc := readJSONDocument(t, projectLocalPath)
	if localDoc["theme"] != "local" {
		t.Fatalf("expected local settings to preserve unrelated fields, got %v", localDoc["theme"])
	}
	disabled := stringSlice(localDoc["disabledMcpjsonServers"])
	if len(disabled) != 1 || disabled[0] != "shadowed-server" {
		t.Fatalf("unexpected local disabledMcpjsonServers %v", disabled)
	}
	excludes := stringSlice(localDoc["claudeMdExcludes"])
	if len(excludes) != 1 || excludes[0] != instructionPath {
		t.Fatalf("unexpected local claudeMdExcludes %v", excludes)
	}
	if got := readFile(t, projectSettingsPath); got != managedBefore {
		t.Fatal("expected managed project settings to remain unchanged")
	}
}

func TestClaudeExecutorRevokesDirectoriesFromTopLevelAndProjectState(t *testing.T) {
	t.Parallel()

	homeDir := filepath.Join(t.TempDir(), "home")
	statePath := filepath.Join(homeDir, ".claude.json")
	trustedPath := filepath.Join(homeDir, "workspace-alpha")
	additionalPath := filepath.Join(homeDir, "workspace-beta")
	keepPath := filepath.Join(homeDir, "workspace-keep")
	writeFile(t, statePath, `{
  "trustedDirectories": ["`+trustedPath+`", "`+keepPath+`"],
  "additionalDirectories": ["`+additionalPath+`", "`+keepPath+`"],
  "projects": {
    "`+trustedPath+`": {"trusted": true, "trustLevel": "trusted", "note": "keep"},
    "`+additionalPath+`": {"additional": true, "trustLevel": "additional", "note": "keep"},
    "`+keepPath+`": {"trusted": true}
  }
}
`)

	executor := NewClaudeExecutor(homeDir)
	results := executeTasks(t, executor, []spmapi.EnforcementTask{
		{
			ID:     "task-revoke-trusted",
			Action: "revoke_trusted_directory",
			Payload: map[string]any{
				"directory_path": trustedPath,
			},
		},
		{
			ID:     "task-revoke-additional",
			Action: "revoke_additional_directory",
			Payload: map[string]any{
				"directory_path": additionalPath,
			},
		},
	})

	if results[0].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected trusted revoke status %q", results[0].Status)
	}
	if results[1].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected additional revoke status %q", results[1].Status)
	}

	doc := readJSONDocument(t, statePath)
	if containsString(stringSlice(doc["trustedDirectories"]), trustedPath) {
		t.Fatal("expected trusted directory to be removed from top-level array")
	}
	if containsString(stringSlice(doc["additionalDirectories"]), additionalPath) {
		t.Fatal("expected additional directory to be removed from top-level array")
	}

	projects := doc["projects"].(map[string]any)
	trustedProject := projects[trustedPath].(map[string]any)
	if _, ok := trustedProject["trusted"]; ok {
		t.Fatal("expected trusted project flag to be removed")
	}
	if _, ok := trustedProject["trustLevel"]; ok {
		t.Fatal("expected trusted project trustLevel to be removed")
	}
	if trustedProject["note"] != "keep" {
		t.Fatalf("expected unrelated trusted project fields to be preserved, got %v", trustedProject["note"])
	}

	additionalProject := projects[additionalPath].(map[string]any)
	if _, ok := additionalProject["additional"]; ok {
		t.Fatal("expected additional project flag to be removed")
	}
	if _, ok := additionalProject["trustLevel"]; ok {
		t.Fatal("expected additional project trustLevel to be removed")
	}
	if additionalProject["note"] != "keep" {
		t.Fatalf("expected unrelated additional project fields to be preserved, got %v", additionalProject["note"])
	}

	retryResults := executeTasks(t, executor, []spmapi.EnforcementTask{
		{
			ID:     "task-revoke-trusted",
			Action: "revoke_trusted_directory",
			Payload: map[string]any{
				"directory_path": trustedPath,
			},
		},
		{
			ID:     "task-revoke-additional",
			Action: "revoke_additional_directory",
			Payload: map[string]any{
				"directory_path": additionalPath,
			},
		},
	})

	if retryResults[0].Status != spmapi.TaskResultStatusSkipped {
		t.Fatalf("expected trusted retry to skip, got %q", retryResults[0].Status)
	}
	if retryResults[1].Status != spmapi.TaskResultStatusSkipped {
		t.Fatalf("expected additional retry to skip, got %q", retryResults[1].Status)
	}
}

func TestClaudeExecutorReconcilesPermissionAndSandboxConfig(t *testing.T) {
	t.Parallel()

	homeDir := filepath.Join(t.TempDir(), "home")
	settingsPath := filepath.Join(homeDir, ".claude", "settings.json")
	writeFile(t, settingsPath, `{
  "theme": "light",
  "permissions": {"allow": ["Read"]},
  "sandbox": {"mode": "workspace-write"}
}
`)

	executor := NewClaudeExecutor(homeDir)
	results := executeTasks(t, executor, []spmapi.EnforcementTask{
		{
			ID:     "task-permissions",
			Action: "reconcile_permission_config",
			Payload: map[string]any{
				"target_path": settingsPath,
				"value": map[string]any{
					"allow": []any{"Read", "Write"},
				},
			},
		},
		{
			ID:     "task-sandbox",
			Action: "reconcile_sandbox_config",
			Payload: map[string]any{
				"target_path": settingsPath,
				"value": map[string]any{
					"mode": "workspace-write",
				},
			},
		},
	})

	if results[0].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected permission status %q", results[0].Status)
	}
	if results[1].Status != spmapi.TaskResultStatusSkipped {
		t.Fatalf("unexpected sandbox status %q", results[1].Status)
	}

	doc := readJSONDocument(t, settingsPath)
	if doc["theme"] != "light" {
		t.Fatalf("expected unrelated setting to be preserved, got %v", doc["theme"])
	}
	if !reflectDeepEqual(doc["permissions"], map[string]any{"allow": []any{"Read", "Write"}}) {
		t.Fatalf("unexpected permissions value %v", doc["permissions"])
	}
	if !reflectDeepEqual(doc["sandbox"], map[string]any{"mode": "workspace-write"}) {
		t.Fatalf("unexpected sandbox value %v", doc["sandbox"])
	}
}

func TestClaudeExecutorDisablesHookAndSkillWithoutTouchingOtherEntries(t *testing.T) {
	t.Parallel()

	homeDir := filepath.Join(t.TempDir(), "home")
	statePath := filepath.Join(homeDir, ".claude.json")
	projectLocalPath := filepath.Join(homeDir, "workspace-alpha", ".claude", "settings.local.json")
	writeFile(t, statePath, `{
  "hooks": {
    "PreToolUse": [
      {"matcher":".*","command":"echo audit"},
      {"matcher":"write","command":"echo keep"}
    ],
    "PostToolUse": [
      {"matcher":".*","command":"echo post"}
    ]
  }
}
`)
	writeFile(t, projectLocalPath, `{
  "skills": ["keep-skill", "remove-skill"]
}
`)

	executor := NewClaudeExecutor(homeDir)
	results := executeTasks(t, executor, []spmapi.EnforcementTask{
		{
			ID:     "task-hook",
			Action: "disable_hook",
			Payload: map[string]any{
				"fingerprint": "PreToolUse|.*|echo audit|0",
			},
		},
		{
			ID:     "task-skill",
			Action: "disable_skill",
			Payload: map[string]any{
				"fingerprint": "remove-skill",
				"target_path": projectLocalPath,
			},
		},
	})

	if results[0].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected hook status %q", results[0].Status)
	}
	if results[1].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected skill status %q", results[1].Status)
	}

	hookDoc := readJSONDocument(t, statePath)
	hooks := hookDoc["hooks"].(map[string]any)
	preToolUse := hooks["PreToolUse"].([]any)
	if len(preToolUse) != 1 {
		t.Fatalf("expected only one PreToolUse hook to remain, got %d", len(preToolUse))
	}
	if hookDisplay := preToolUse[0].(map[string]any)["command"]; hookDisplay != "echo keep" {
		t.Fatalf("unexpected remaining hook command %v", hookDisplay)
	}
	if _, ok := hooks["PostToolUse"]; !ok {
		t.Fatal("expected unrelated hook event to be preserved")
	}

	skillDoc := readJSONDocument(t, projectLocalPath)
	skills := stringSlice(skillDoc["skills"])
	if len(skills) != 1 || skills[0] != "keep-skill" {
		t.Fatalf("unexpected skills %v", skills)
	}

	retryResults := executeTasks(t, executor, []spmapi.EnforcementTask{
		{
			ID:     "task-hook",
			Action: "disable_hook",
			Payload: map[string]any{
				"fingerprint": "PreToolUse|.*|echo audit|0",
			},
		},
		{
			ID:     "task-skill",
			Action: "disable_skill",
			Payload: map[string]any{
				"fingerprint": "remove-skill",
				"target_path": projectLocalPath,
			},
		},
	})

	if retryResults[0].Status != spmapi.TaskResultStatusSkipped {
		t.Fatalf("expected hook retry to skip, got %q", retryResults[0].Status)
	}
	if retryResults[1].Status != spmapi.TaskResultStatusSkipped {
		t.Fatalf("expected skill retry to skip, got %q", retryResults[1].Status)
	}
}

func TestClaudeExecutorRejectsForbiddenTargetPathsWithoutMutatingFiles(t *testing.T) {
	t.Parallel()

	homeDir := filepath.Join(t.TempDir(), "home")
	projectRoot := filepath.Join(homeDir, "workspace-alpha")
	projectSettingsPath := filepath.Join(projectRoot, ".claude", "settings.json")
	projectMCPPath := filepath.Join(projectRoot, ".mcp.json")
	claudePath := filepath.Join(projectRoot, "CLAUDE.md")
	agentsPath := filepath.Join(projectRoot, "AGENTS.md")

	writeFile(t, projectSettingsPath, "{\n  \"permissions\": {\"allow\": [\"Read\"]}\n}\n")
	writeFile(t, projectMCPPath, "{\n  \"mcpServers\": {\n    \"github\": {}\n  }\n}\n")
	writeFile(t, claudePath, "# Claude instructions\n")
	writeFile(t, agentsPath, "# Agents instructions\n")

	testCases := []struct {
		name     string
		task     spmapi.EnforcementTask
		filePath string
	}{
		{
			name: "project settings json",
			task: spmapi.EnforcementTask{
				ID:     "task-forbidden-settings",
				Action: "reconcile_permission_config",
				Payload: map[string]any{
					"target_path": projectSettingsPath,
					"value":       map[string]any{"allow": []any{"Read", "Write"}},
				},
			},
			filePath: projectSettingsPath,
		},
		{
			name: "project mcp json",
			task: spmapi.EnforcementTask{
				ID:     "task-forbidden-mcp",
				Action: "disable_mcp_server",
				Payload: map[string]any{
					"server_name": "github",
					"target_path": projectMCPPath,
				},
			},
			filePath: projectMCPPath,
		},
		{
			name: "claude markdown",
			task: spmapi.EnforcementTask{
				ID:     "task-forbidden-claude",
				Action: "exclude_instruction_file",
				Payload: map[string]any{
					"file_path":    claudePath,
					"project_root": projectRoot,
					"target_path":  claudePath,
				},
			},
			filePath: claudePath,
		},
		{
			name: "agents markdown",
			task: spmapi.EnforcementTask{
				ID:     "task-forbidden-agents",
				Action: "disable_skill",
				Payload: map[string]any{
					"fingerprint": "remove-skill",
					"target_path": agentsPath,
				},
			},
			filePath: agentsPath,
		},
	}

	executor := NewClaudeExecutor(homeDir)
	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			original := readFile(t, tc.filePath)
			results := executeTasks(t, executor, []spmapi.EnforcementTask{tc.task})
			if results[0].Status != spmapi.TaskResultStatusFailed {
				t.Fatalf("expected failed status, got %q", results[0].Status)
			}
			if got := readFile(t, tc.filePath); got != original {
				t.Fatalf("expected %s to remain unchanged", tc.filePath)
			}
		})
	}
}

func executeTasks(
	t *testing.T,
	executor ClaudeExecutor,
	tasks []spmapi.EnforcementTask,
) []spmapi.SyncTaskResult {
	t.Helper()

	results, err := executor.Execute(context.Background(), tasks)
	if err != nil {
		t.Fatalf("Execute() error = %v", err)
	}
	return results
}

func copyInventoryFixture(t *testing.T, fixtureName string) string {
	t.Helper()

	root := filepath.Join("..", "inventory", "testdata", fixtureName, "home")
	targetHome := filepath.Join(t.TempDir(), "home")
	if err := copyFixtureDirectory(root, targetHome, targetHome); err != nil {
		t.Fatalf("copy fixture %s: %v", fixtureName, err)
	}
	return targetHome
}

func copyFixtureDirectory(source string, target string, homeDir string) error {
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
			if err := copyFixtureDirectory(sourcePath, targetPath, homeDir); err != nil {
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

func writeFile(t *testing.T, path string, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir %s: %v", path, err)
	}
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return string(data)
}

func readJSONDocument(t *testing.T, path string) map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	var doc map[string]any
	if err := json.Unmarshal(data, &doc); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	return doc
}

func reflectDeepEqual(left any, right any) bool {
	leftJSON, _ := json.Marshal(left)
	rightJSON, _ := json.Marshal(right)
	return string(leftJSON) == string(rightJSON)
}
