package tasks

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/spmapi"
)

func TestClaudeExecutorDisablesProjectMCPViaDisabledMcpjsonServers(t *testing.T) {
	t.Parallel()

	homeDir := filepath.Join(t.TempDir(), "home")
	projectRoot := filepath.Join(homeDir, "workspace-alpha")
	projectMCPPath := filepath.Join(projectRoot, ".mcp.json")
	if err := os.MkdirAll(filepath.Dir(projectMCPPath), 0o755); err != nil {
		t.Fatalf("mkdir project: %v", err)
	}
	if err := os.WriteFile(projectMCPPath, []byte("{\"mcpServers\":{\"github\":{}}}\n"), 0o600); err != nil {
		t.Fatalf("write .mcp.json: %v", err)
	}

	executor := NewClaudeExecutor(homeDir)
	task := spmapi.EnforcementTask{
		ID:     "task-1",
		Action: "disable_mcp_server",
		Payload: map[string]any{
			"server_name":  "github",
			"project_root": projectRoot,
			"source_path":  projectMCPPath,
		},
	}

	results, err := executor.Execute(context.Background(), []spmapi.EnforcementTask{task})
	if err != nil {
		t.Fatalf("Execute() error = %v", err)
	}
	if results[0].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected status %q", results[0].Status)
	}

	localSettingsPath := filepath.Join(projectRoot, ".claude", "settings.local.json")
	doc := readJSONDocument(t, localSettingsPath)
	disabled := stringSlice(doc["disabledMcpjsonServers"])
	if len(disabled) != 1 || disabled[0] != "github" {
		t.Fatalf("unexpected disabledMcpjsonServers %v", disabled)
	}

	originalMCP := readJSONDocument(t, projectMCPPath)
	if _, ok := originalMCP["mcpServers"]; !ok {
		t.Fatal("expected .mcp.json to remain untouched")
	}

	results, err = executor.Execute(context.Background(), []spmapi.EnforcementTask{task})
	if err != nil {
		t.Fatalf("Execute() second error = %v", err)
	}
	if results[0].Status != spmapi.TaskResultStatusSkipped {
		t.Fatalf("expected idempotent skip, got %q", results[0].Status)
	}
}

func TestClaudeExecutorExcludesInstructionFileByPath(t *testing.T) {
	t.Parallel()

	homeDir := filepath.Join(t.TempDir(), "home")
	projectRoot := filepath.Join(homeDir, "workspace-alpha")
	instructionPath := filepath.Join(projectRoot, "CLAUDE.md")

	executor := NewClaudeExecutor(homeDir)
	task := spmapi.EnforcementTask{
		ID:     "task-2",
		Action: "exclude_instruction_file",
		Payload: map[string]any{
			"file_path":    instructionPath,
			"project_root": projectRoot,
		},
	}

	results, err := executor.Execute(context.Background(), []spmapi.EnforcementTask{task})
	if err != nil {
		t.Fatalf("Execute() error = %v", err)
	}
	if results[0].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected status %q", results[0].Status)
	}

	localSettingsPath := filepath.Join(projectRoot, ".claude", "settings.local.json")
	doc := readJSONDocument(t, localSettingsPath)
	excludes := stringSlice(doc["claudeMdExcludes"])
	if len(excludes) != 1 || excludes[0] != instructionPath {
		t.Fatalf("unexpected claudeMdExcludes %v", excludes)
	}

	results, err = executor.Execute(context.Background(), []spmapi.EnforcementTask{task})
	if err != nil {
		t.Fatalf("Execute() second error = %v", err)
	}
	if results[0].Status != spmapi.TaskResultStatusSkipped {
		t.Fatalf("expected idempotent skip, got %q", results[0].Status)
	}
}

func TestClaudeExecutorReconcilesUserConfigWithoutDroppingUnrelatedSettings(t *testing.T) {
	t.Parallel()

	homeDir := filepath.Join(t.TempDir(), "home")
	settingsPath := filepath.Join(homeDir, ".claude", "settings.json")
	if err := os.MkdirAll(filepath.Dir(settingsPath), 0o755); err != nil {
		t.Fatalf("mkdir settings dir: %v", err)
	}
	if err := os.WriteFile(settingsPath, []byte("{\"theme\":\"light\",\"permissions\":{\"allow\":[\"Read\"]}}\n"), 0o600); err != nil {
		t.Fatalf("write settings.json: %v", err)
	}

	executor := NewClaudeExecutor(homeDir)
	task := spmapi.EnforcementTask{
		ID:     "task-3",
		Action: "reconcile_permission_config",
		Payload: map[string]any{
			"target_path": settingsPath,
			"value": map[string]any{
				"allow": []any{"Read", "Write"},
			},
		},
	}

	results, err := executor.Execute(context.Background(), []spmapi.EnforcementTask{task})
	if err != nil {
		t.Fatalf("Execute() error = %v", err)
	}
	if results[0].Status != spmapi.TaskResultStatusApplied {
		t.Fatalf("unexpected status %q", results[0].Status)
	}

	doc := readJSONDocument(t, settingsPath)
	if doc["theme"] != "light" {
		t.Fatalf("expected unrelated setting to be preserved, got %v", doc["theme"])
	}
	if !reflectDeepEqual(doc["permissions"], map[string]any{"allow": []any{"Read", "Write"}}) {
		t.Fatalf("unexpected permissions value %v", doc["permissions"])
	}
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
