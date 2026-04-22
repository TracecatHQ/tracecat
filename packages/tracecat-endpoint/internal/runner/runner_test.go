package runner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/spmapi"
	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/state"
)

type staticInventory struct {
	assets []spmapi.SyncAsset
}

func (s staticInventory) Collect(context.Context) ([]spmapi.SyncAsset, error) {
	return s.assets, nil
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
