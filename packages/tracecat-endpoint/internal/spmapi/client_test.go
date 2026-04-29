package spmapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestSyncEndpoint(t *testing.T) {
	t.Parallel()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/spm/endpoints/endpoint-123/sync" {
			t.Fatalf("unexpected path %q", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer endpoint-secret" {
			t.Fatalf("unexpected authorization %q", got)
		}

		var payload SyncRequest
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatalf("decode request: %v", err)
		}
		if payload.Name != "test-host" {
			t.Fatalf("unexpected request name %q", payload.Name)
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(SyncResponse{
			Endpoint: Endpoint{
				ID:             "endpoint-123",
				OrganizationID: "org-123",
				Name:           "test-host",
				Status:         EndpointStatusActive,
				CreatedAt:      time.Now().UTC(),
				UpdatedAt:      time.Now().UTC(),
			},
			EndpointSecret: "tcspm_ep_secret",
			Tasks: []EnforcementTask{
				{
					ID:        "task-123",
					Action:    "disable_mcp_server",
					Status:    "pending",
					CreatedAt: time.Now().UTC(),
					UpdatedAt: time.Now().UTC(),
				},
			},
		})
	}))
	defer server.Close()

	client, err := NewClient(server.URL, server.Client())
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}

	resp, err := client.SyncEndpoint(context.Background(), "endpoint-123", "endpoint-secret", SyncRequest{
		Name:            "test-host",
		Status:          EndpointStatusActive,
		InventoryItems:  []SyncInventoryItem{},
		TaskResults:     []SyncTaskResult{},
		ClientMetadata:  map[string]any{"binary": "tracecatd"},
		EndpointVersion: "0.1.0",
	})
	if err != nil {
		t.Fatalf("SyncEndpoint() error = %v", err)
	}
	if resp.EndpointSecret != "tcspm_ep_secret" {
		t.Fatalf("unexpected endpoint secret %q", resp.EndpointSecret)
	}
	if len(resp.Tasks) != 1 {
		t.Fatalf("expected one task, got %d", len(resp.Tasks))
	}
}
