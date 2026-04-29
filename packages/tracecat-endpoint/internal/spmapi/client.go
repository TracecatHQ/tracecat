package spmapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type EndpointStatus string

const (
	EndpointStatusPending  EndpointStatus = "pending"
	EndpointStatusActive   EndpointStatus = "active"
	EndpointStatusError    EndpointStatus = "error"
	EndpointStatusDisabled EndpointStatus = "disabled"
)

type EnforcementAction string

type EnforcementTaskStatus string

type TaskResultStatus string

const (
	TaskResultStatusApplied TaskResultStatus = "applied"
	TaskResultStatusFailed  TaskResultStatus = "failed"
	TaskResultStatusSkipped TaskResultStatus = "skipped"
)

type Endpoint struct {
	ID              string         `json:"id"`
	OrganizationID  string         `json:"organization_id"`
	Name            string         `json:"name"`
	Platform        string         `json:"platform"`
	Status          EndpointStatus `json:"status"`
	EndpointVersion string         `json:"endpoint_version"`
	Hostname        string         `json:"hostname"`
	OSUser          string         `json:"os_user"`
	HomePath        string         `json:"home_path"`
	ClientMetadata  map[string]any `json:"client_metadata"`
	CreatedAt       time.Time      `json:"created_at"`
	UpdatedAt       time.Time      `json:"updated_at"`
}

type SyncAsset struct {
	Harness          string         `json:"harness"`
	AssetType        string         `json:"asset_type"`
	ArtifactType     string         `json:"artifact_type"`
	ArtifactLocation string         `json:"artifact_location"`
	IdentityKey      string         `json:"identity_key"`
	DisplayName      string         `json:"display_name"`
	ContentHash      string         `json:"content_hash,omitempty"`
	WorkspaceID      string         `json:"workspace_id,omitempty"`
	Metadata         map[string]any `json:"metadata,omitempty"`
	Evidence         map[string]any `json:"evidence,omitempty"`
	ObservedState    map[string]any `json:"observed_state,omitempty"`
}

type SyncTaskResult struct {
	TaskID      string           `json:"task_id"`
	Status      TaskResultStatus `json:"status"`
	Result      map[string]any   `json:"result,omitempty"`
	Error       string           `json:"error,omitempty"`
	CompletedAt time.Time        `json:"completed_at"`
}

type EnforcementTask struct {
	ID                string                `json:"id"`
	OrganizationID    string                `json:"organization_id"`
	EndpointID        string                `json:"endpoint_id"`
	FindingID         string                `json:"finding_id,omitempty"`
	Action            EnforcementAction     `json:"action"`
	Payload           map[string]any        `json:"payload,omitempty"`
	Status            EnforcementTaskStatus `json:"status"`
	RequestedByUserID string                `json:"requested_by_user_id,omitempty"`
	CompletedAt       *time.Time            `json:"completed_at,omitempty"`
	Result            map[string]any        `json:"result,omitempty"`
	Error             string                `json:"error,omitempty"`
	CreatedAt         time.Time             `json:"created_at"`
	UpdatedAt         time.Time             `json:"updated_at"`
}

type SyncRequest struct {
	Name            string           `json:"name,omitempty"`
	EndpointVersion string           `json:"endpoint_version,omitempty"`
	Hostname        string           `json:"hostname,omitempty"`
	OSUser          string           `json:"os_user,omitempty"`
	HomePath        string           `json:"home_path,omitempty"`
	Status          EndpointStatus   `json:"status"`
	ClientMetadata  map[string]any   `json:"client_metadata,omitempty"`
	Assets          []SyncAsset      `json:"assets"`
	TaskResults     []SyncTaskResult `json:"task_results"`
}

type SyncResponse struct {
	Endpoint       Endpoint          `json:"endpoint"`
	EndpointSecret string            `json:"endpoint_secret,omitempty"`
	Tasks          []EnforcementTask `json:"tasks"`
}

type Client struct {
	baseURL    *url.URL
	httpClient *http.Client
}

func NewClient(serverURL string, httpClient *http.Client) (*Client, error) {
	parsed, err := url.Parse(strings.TrimSpace(serverURL))
	if err != nil {
		return nil, fmt.Errorf("parse server url: %w", err)
	}
	if parsed.Scheme == "" || parsed.Host == "" {
		return nil, fmt.Errorf("invalid server url: %q", serverURL)
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 30 * time.Second}
	}
	return &Client{baseURL: parsed, httpClient: httpClient}, nil
}

func (c *Client) SyncEndpoint(
	ctx context.Context,
	endpointID string,
	bearerToken string,
	payload SyncRequest,
) (*SyncResponse, error) {
	path := fmt.Sprintf("/spm/endpoints/%s/sync", endpointID)
	requestURL := c.baseURL.ResolveReference(&url.URL{Path: path})

	body, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("encode sync request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, requestURL.String(), bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create sync request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+bearerToken)
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("send sync request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= http.StatusBadRequest {
		message, readErr := io.ReadAll(io.LimitReader(resp.Body, 32*1024))
		if readErr != nil {
			return nil, fmt.Errorf("sync request failed with status %d", resp.StatusCode)
		}
		return nil, fmt.Errorf("sync request failed with status %d: %s", resp.StatusCode, strings.TrimSpace(string(message)))
	}

	var result SyncResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode sync response: %w", err)
	}
	if result.Tasks == nil {
		result.Tasks = []EnforcementTask{}
	}
	return &result, nil
}
