package runner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/spmapi"
	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/state"
)

type staticInventory struct {
	items         []spmapi.SyncInventoryItem
	relationships []spmapi.SyncInventoryRelationship
}

func (s staticInventory) Collect(context.Context) (spmapi.InventorySnapshot, error) {
	return spmapi.InventorySnapshot{
		InventoryItems: s.items,
		Relationships:  s.relationships,
	}, nil
}

func TestRunOnceRedeemsEnrollmentTokenAndFlushesTaskResults(t *testing.T) {
	t.Parallel()

	var requests atomic.Int32
	var projectRoot string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		call := requests.Add(1)
		if got := r.Header.Get("Authorization"); call == 1 && got != "Bearer enrollment-token" {
			t.Fatalf("unexpected first auth header %q", got)
		}
		if got := r.Header.Get("Authorization"); call == 2 && got != "Bearer endpoint-secret" {
			t.Fatalf("unexpected second auth header %q", got)
		}

		var req spmapi.SyncRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Fatalf("decode request: %v", err)
		}

		w.Header().Set("Content-Type", "application/json")
		switch call {
		case 1:
			if len(req.TaskResults) != 0 {
				t.Fatalf("expected no initial task results, got %d", len(req.TaskResults))
			}
			_ = json.NewEncoder(w).Encode(spmapi.SyncResponse{
				Endpoint: spmapi.Endpoint{
					ID:             "endpoint-123",
					OrganizationID: "org-123",
					Name:           req.Name,
					Status:         spmapi.EndpointStatusActive,
					CreatedAt:      time.Now().UTC(),
					UpdatedAt:      time.Now().UTC(),
				},
				EndpointSecret: "endpoint-secret",
				Tasks: []spmapi.EnforcementTask{
					{
						ID:     "task-123",
						Action: "disable_mcp_server",
						Payload: map[string]any{
							"server_name":  "github",
							"project_root": projectRoot,
						},
						Status:    "pending",
						CreatedAt: time.Now().UTC(),
						UpdatedAt: time.Now().UTC(),
					},
				},
			})
		case 2:
			if len(req.TaskResults) != 1 {
				t.Fatalf("expected flushed task result, got %d", len(req.TaskResults))
			}
			if req.TaskResults[0].Status != spmapi.TaskResultStatusApplied {
				t.Fatalf("unexpected task result status %q", req.TaskResults[0].Status)
			}
			_ = json.NewEncoder(w).Encode(spmapi.SyncResponse{
				Endpoint: spmapi.Endpoint{
					ID:             "endpoint-123",
					OrganizationID: "org-123",
					Name:           req.Name,
					Status:         spmapi.EndpointStatusActive,
					CreatedAt:      time.Now().UTC(),
					UpdatedAt:      time.Now().UTC(),
				},
				Tasks: []spmapi.EnforcementTask{},
			})
		default:
			t.Fatalf("unexpected sync call %d", call)
		}
	}))
	defer server.Close()

	root := t.TempDir()
	homeDir := filepath.Join(root, "home")
	projectRoot = filepath.Join(homeDir, "project")
	service, err := New(Options{
		ServerURL:       server.URL,
		StateDir:        filepath.Join(root, ".tracecatd"),
		HomeDir:         homeDir,
		EndpointID:      "endpoint-123",
		EnrollmentToken: "enrollment-token",
		HTTPClient:      server.Client(),
		Inventory:       staticInventory{},
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	if err := service.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce() error = %v", err)
	}
	if got := requests.Load(); got != 2 {
		t.Fatalf("expected 2 sync requests, got %d", got)
	}

	store := state.NewStore(service.StateDir())
	st, err := store.Load()
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	if st.TokenKind != state.TokenKindEndpoint {
		t.Fatalf("expected endpoint token kind, got %q", st.TokenKind)
	}
	if st.Token != "endpoint-secret" {
		t.Fatalf("unexpected endpoint token %q", st.Token)
	}
	if len(st.PendingTaskResults) != 0 {
		t.Fatalf("expected no pending task results, got %d", len(st.PendingTaskResults))
	}
}

func TestRunOncePreservesQueuedResultsWhenFollowUpSyncFails(t *testing.T) {
	t.Parallel()

	var requests atomic.Int32
	var projectRoot string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		call := requests.Add(1)
		w.Header().Set("Content-Type", "application/json")
		if call == 1 {
			_ = json.NewEncoder(w).Encode(spmapi.SyncResponse{
				Endpoint: spmapi.Endpoint{
					ID:             "endpoint-123",
					OrganizationID: "org-123",
					Name:           "host",
					Status:         spmapi.EndpointStatusActive,
					CreatedAt:      time.Now().UTC(),
					UpdatedAt:      time.Now().UTC(),
				},
				EndpointSecret: "endpoint-secret",
				Tasks: []spmapi.EnforcementTask{
					{
						ID:     "task-123",
						Action: "disable_mcp_server",
						Payload: map[string]any{
							"server_name":  "github",
							"project_root": projectRoot,
						},
						Status:    "pending",
						CreatedAt: time.Now().UTC(),
						UpdatedAt: time.Now().UTC(),
					},
				},
			})
			return
		}
		http.Error(w, `{"detail":"boom"}`, http.StatusBadGateway)
	}))
	defer server.Close()

	root := t.TempDir()
	projectRoot = filepath.Join(root, "home", "project")
	service, err := New(Options{
		ServerURL:       server.URL,
		StateDir:        filepath.Join(root, ".tracecatd"),
		HomeDir:         filepath.Join(root, "home"),
		EndpointID:      "endpoint-123",
		EnrollmentToken: "enrollment-token",
		HTTPClient:      server.Client(),
		Inventory:       staticInventory{},
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	if err := service.RunOnce(context.Background()); err == nil {
		t.Fatal("expected follow-up sync error")
	}

	store := state.NewStore(service.StateDir())
	st, err := store.Load()
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	if st.Token != "endpoint-secret" {
		t.Fatalf("unexpected token %q", st.Token)
	}
	if len(st.PendingTaskResults) != 1 {
		t.Fatalf("expected preserved task result, got %d", len(st.PendingTaskResults))
	}
}

func TestRunOnceWithClaudeFixtureFlushesRealTaskResultsAndStaysIdempotent(t *testing.T) {
	t.Parallel()

	homeDir := copyInventoryFixture(t, "claude")
	statePath := filepath.Join(homeDir, ".claude.json")
	sourcePath := statePath

	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		call := requests.Add(1)
		var req spmapi.SyncRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Fatalf("decode request: %v", err)
		}

		localStdio := findRequestItem(t, req.InventoryItems, "local-stdio")
		disabled, _ := localStdio.ObservedState["disabled"].(bool)

		w.Header().Set("Content-Type", "application/json")
		switch call {
		case 1:
			if got := r.Header.Get("Authorization"); got != "Bearer enrollment-token" {
				t.Fatalf("unexpected first auth header %q", got)
			}
			if len(req.TaskResults) != 0 {
				t.Fatalf("expected no initial task results, got %d", len(req.TaskResults))
			}
			if disabled {
				t.Fatal("expected fixture MCP server to start enabled")
			}
			_ = json.NewEncoder(w).Encode(syncResponse(req.Name, "endpoint-secret", []spmapi.EnforcementTask{
				{
					ID:     "task-123",
					Action: "disable_mcp_server",
					Payload: map[string]any{
						"server_name":       "local-stdio",
						"resolved_identity": "package:@tracecat/mcp",
						"source_path":       sourcePath,
					},
					Status:    "pending",
					CreatedAt: time.Now().UTC(),
					UpdatedAt: time.Now().UTC(),
				},
			}))
		case 2:
			if got := r.Header.Get("Authorization"); got != "Bearer endpoint-secret" {
				t.Fatalf("unexpected second auth header %q", got)
			}
			if len(req.TaskResults) != 1 || req.TaskResults[0].Status != spmapi.TaskResultStatusApplied {
				t.Fatalf("expected applied task result, got %+v", req.TaskResults)
			}
			if disabled {
				t.Fatal("expected immediate follow-up sync to reuse the pre-execution inventory snapshot")
			}
			_ = json.NewEncoder(w).Encode(syncResponse(req.Name, "", nil))
		case 3:
			if got := r.Header.Get("Authorization"); got != "Bearer endpoint-secret" {
				t.Fatalf("unexpected third auth header %q", got)
			}
			if len(req.TaskResults) != 0 {
				t.Fatalf("expected no queued task results on next cycle, got %d", len(req.TaskResults))
			}
			if !disabled {
				t.Fatal("expected local-stdio MCP server to remain disabled")
			}
			_ = json.NewEncoder(w).Encode(syncResponse(req.Name, "", []spmapi.EnforcementTask{
				{
					ID:     "task-456",
					Action: "disable_mcp_server",
					Payload: map[string]any{
						"server_name":       "local-stdio",
						"resolved_identity": "package:@tracecat/mcp",
						"source_path":       sourcePath,
					},
					Status:    "pending",
					CreatedAt: time.Now().UTC(),
					UpdatedAt: time.Now().UTC(),
				},
			}))
		case 4:
			if len(req.TaskResults) != 1 || req.TaskResults[0].Status != spmapi.TaskResultStatusSkipped {
				t.Fatalf("expected skipped task result on idempotent retry, got %+v", req.TaskResults)
			}
			if !disabled {
				t.Fatal("expected local-stdio MCP server to stay disabled on retry")
			}
			_ = json.NewEncoder(w).Encode(syncResponse(req.Name, "", nil))
		default:
			t.Fatalf("unexpected sync call %d", call)
		}
	}))
	defer server.Close()

	root := t.TempDir()
	service, err := New(Options{
		ServerURL:       server.URL,
		StateDir:        filepath.Join(root, ".tracecatd"),
		HomeDir:         homeDir,
		EndpointID:      "endpoint-123",
		EnrollmentToken: "enrollment-token",
		HTTPClient:      server.Client(),
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	if err := service.RunOnce(context.Background()); err != nil {
		t.Fatalf("first RunOnce() error = %v", err)
	}
	if err := service.RunOnce(context.Background()); err != nil {
		t.Fatalf("second RunOnce() error = %v", err)
	}
	if got := requests.Load(); got != 4 {
		t.Fatalf("expected 4 sync requests, got %d", got)
	}

	doc := readJSONDocument(t, statePath)
	servers := doc["mcpServers"].(map[string]any)
	local := servers["local-stdio"].(map[string]any)
	if disabled, _ := local["disabled"].(bool); !disabled {
		t.Fatal("expected local-stdio server to remain disabled after runner cycles")
	}

	store := state.NewStore(service.StateDir())
	st, err := store.Load()
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	if st.Token != "endpoint-secret" {
		t.Fatalf("unexpected endpoint token %q", st.Token)
	}
	if len(st.PendingTaskResults) != 0 {
		t.Fatalf("expected no pending task results after idempotent retry, got %d", len(st.PendingTaskResults))
	}
}

func TestRunOnceWithRogueMCPFixtureWritesProjectLocalSettingsWithoutMutatingProjectMCP(t *testing.T) {
	t.Parallel()

	homeDir := copyE2EFixture(t, "rogue_mcp")
	projectRoot := filepath.Join(homeDir, "workspace-alpha")
	projectMCPPath := filepath.Join(projectRoot, ".mcp.json")
	localSettingsPath := filepath.Join(projectRoot, ".claude", "settings.local.json")

	originalProjectMCP, err := os.ReadFile(projectMCPPath)
	if err != nil {
		t.Fatalf("read original project mcp: %v", err)
	}

	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		call := requests.Add(1)
		var req spmapi.SyncRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Fatalf("decode request: %v", err)
		}

		w.Header().Set("Content-Type", "application/json")
		switch call {
		case 1:
			if got := r.Header.Get("Authorization"); got != "Bearer enrollment-token" {
				t.Fatalf("unexpected first auth header %q", got)
			}
			if len(req.TaskResults) != 0 {
				t.Fatalf("expected no initial task results, got %d", len(req.TaskResults))
			}
			_ = json.NewEncoder(w).Encode(syncResponse(req.Name, "endpoint-secret", []spmapi.EnforcementTask{
				{
					ID:     "task-project-mcp-1",
					Action: "disable_mcp_server",
					Payload: map[string]any{
						"server_name":  "slack",
						"project_root": projectRoot,
						"source_path":  projectMCPPath,
					},
					Status:    "pending",
					CreatedAt: time.Now().UTC(),
					UpdatedAt: time.Now().UTC(),
				},
			}))
		case 2:
			if got := r.Header.Get("Authorization"); got != "Bearer endpoint-secret" {
				t.Fatalf("unexpected second auth header %q", got)
			}
			if len(req.TaskResults) != 1 || req.TaskResults[0].Status != spmapi.TaskResultStatusApplied {
				t.Fatalf("expected applied task result, got %+v", req.TaskResults)
			}
			_ = json.NewEncoder(w).Encode(syncResponse(req.Name, "", nil))
		case 3:
			if got := r.Header.Get("Authorization"); got != "Bearer endpoint-secret" {
				t.Fatalf("unexpected third auth header %q", got)
			}
			if len(req.TaskResults) != 0 {
				t.Fatalf("expected no queued task results on next cycle, got %d", len(req.TaskResults))
			}
			_ = json.NewEncoder(w).Encode(syncResponse(req.Name, "", []spmapi.EnforcementTask{
				{
					ID:     "task-project-mcp-2",
					Action: "disable_mcp_server",
					Payload: map[string]any{
						"server_name":  "slack",
						"project_root": projectRoot,
						"source_path":  projectMCPPath,
					},
					Status:    "pending",
					CreatedAt: time.Now().UTC(),
					UpdatedAt: time.Now().UTC(),
				},
			}))
		case 4:
			if len(req.TaskResults) != 1 || req.TaskResults[0].Status != spmapi.TaskResultStatusSkipped {
				t.Fatalf("expected skipped task result on idempotent retry, got %+v", req.TaskResults)
			}
			_ = json.NewEncoder(w).Encode(syncResponse(req.Name, "", nil))
		default:
			t.Fatalf("unexpected sync call %d", call)
		}
	}))
	defer server.Close()

	root := t.TempDir()
	service, err := New(Options{
		ServerURL:       server.URL,
		StateDir:        filepath.Join(root, ".tracecatd"),
		HomeDir:         homeDir,
		EndpointID:      "endpoint-123",
		EnrollmentToken: "enrollment-token",
		HTTPClient:      server.Client(),
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	if err := service.RunOnce(context.Background()); err != nil {
		t.Fatalf("first RunOnce() error = %v", err)
	}
	if err := service.RunOnce(context.Background()); err != nil {
		t.Fatalf("second RunOnce() error = %v", err)
	}
	if got := requests.Load(); got != 4 {
		t.Fatalf("expected 4 sync requests, got %d", got)
	}

	localSettings := readJSONDocument(t, localSettingsPath)
	disabled, _ := localSettings["disabledMcpjsonServers"].([]any)
	if len(disabled) != 1 || disabled[0] != "slack" {
		t.Fatalf("unexpected disabledMcpjsonServers %v", localSettings["disabledMcpjsonServers"])
	}

	projectMCP := readJSONDocument(t, projectMCPPath)
	servers, ok := projectMCP["mcpServers"].(map[string]any)
	if !ok {
		t.Fatalf("expected mcpServers map, got %T", projectMCP["mcpServers"])
	}
	slack, ok := servers["slack"].(map[string]any)
	if !ok {
		t.Fatalf("expected slack server map, got %T", servers["slack"])
	}
	if _, exists := slack["disabled"]; exists {
		t.Fatalf("expected project .mcp.json to remain unchanged, got %v", slack)
	}

	currentProjectMCP, err := os.ReadFile(projectMCPPath)
	if err != nil {
		t.Fatalf("read current project mcp: %v", err)
	}
	if string(currentProjectMCP) != string(originalProjectMCP) {
		t.Fatal("expected project .mcp.json bytes to remain unchanged")
	}

	store := state.NewStore(service.StateDir())
	st, err := store.Load()
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	if st.Token != "endpoint-secret" {
		t.Fatalf("unexpected endpoint token %q", st.Token)
	}
	if len(st.PendingTaskResults) != 0 {
		t.Fatalf("expected no pending task results after idempotent retry, got %d", len(st.PendingTaskResults))
	}
}

func TestEndpointMetadataUsesPreviewOverrides(t *testing.T) {
	t.Setenv("TRACECAT_DEVICE_NAME", "Preview Rogue Instruction File")
	t.Setenv("TRACECAT_PREVIEW_STACK", "tracecat-preview-devices")
	t.Setenv("TRACECAT_PREVIEW_SCENARIO", "rogue_instruction_file")

	metadata, err := endpointMetadata("/home/tracecat", DefaultInterval)
	if err != nil {
		t.Fatalf("endpointMetadata() error = %v", err)
	}

	if metadata.Name != "Preview Rogue Instruction File" {
		t.Fatalf("expected overridden endpoint name, got %q", metadata.Name)
	}
	if metadata.HomePath != "/home/tracecat" {
		t.Fatalf("unexpected home path %q", metadata.HomePath)
	}
	if got := metadata.ClientMetadata["preview_stack"]; got != "tracecat-preview-devices" {
		t.Fatalf("unexpected preview_stack %v", got)
	}
	if got := metadata.ClientMetadata["preview_scenario"]; got != "rogue_instruction_file" {
		t.Fatalf("unexpected preview_scenario %v", got)
	}
	if got := metadata.ClientMetadata["binary_name"]; got != "tracecatd" {
		t.Fatalf("unexpected binary_name %v", got)
	}
}

func syncResponse(name string, endpointSecret string, tasks []spmapi.EnforcementTask) spmapi.SyncResponse {
	if tasks == nil {
		tasks = []spmapi.EnforcementTask{}
	}
	return spmapi.SyncResponse{
		Endpoint: spmapi.Endpoint{
			ID:             "endpoint-123",
			OrganizationID: "org-123",
			Name:           name,
			Status:         spmapi.EndpointStatusActive,
			CreatedAt:      time.Now().UTC(),
			UpdatedAt:      time.Now().UTC(),
		},
		EndpointSecret: endpointSecret,
		Tasks:          tasks,
	}
}

func findRequestItem(t *testing.T, items []spmapi.SyncInventoryItem, displayName string) spmapi.SyncInventoryItem {
	t.Helper()
	for _, item := range items {
		if item.DisplayName == displayName {
			return item
		}
	}
	t.Fatalf("item with display name %s not found in request", displayName)
	return spmapi.SyncInventoryItem{}
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

func copyE2EFixture(t *testing.T, fixtureName string) string {
	t.Helper()

	root := filepath.Join("..", "..", "e2e", "scenarios", fixtureName, "home")
	targetHome := filepath.Join(t.TempDir(), "home")
	if err := copyFixtureDirectory(root, targetHome, targetHome); err != nil {
		t.Fatalf("copy e2e fixture %s: %v", fixtureName, err)
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
