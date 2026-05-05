package state

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/spmapi"
)

const (
	DefaultDirName = ".tracecatd"
	FileName       = "state.json"
	SchemaName     = "tracecatd-state"
	SchemaVersion  = 1
)

type TokenKind string

const (
	TokenKindEnrollment TokenKind = "enrollment"
	TokenKindEndpoint   TokenKind = "endpoint"
)

type File struct {
	Schema                      string                               `json:"schema"`
	Version                     int                                  `json:"version"`
	ServerURL                   string                               `json:"server_url"`
	EndpointID                  string                               `json:"endpoint_id"`
	TokenKind                   TokenKind                            `json:"token_kind"`
	Token                       string                               `json:"token"`
	HomeDir                     string                               `json:"home_dir"`
	PendingTaskResults          []spmapi.SyncTaskResult              `json:"pending_task_results"`
	PendingActionPreviewResults []spmapi.ResponseActionPreviewResult `json:"pending_action_preview_results"`
}

type BootstrapInput struct {
	ServerURL       string
	EndpointID      string
	EnrollmentToken string
	HomeDir         string
}

type Store struct {
	dir  string
	path string
}

func DefaultDir(userHome string) string {
	return filepath.Join(userHome, DefaultDirName)
}

func NewStore(dir string) *Store {
	return &Store{
		dir:  dir,
		path: filepath.Join(dir, FileName),
	}
}

func (s *Store) Dir() string {
	return s.dir
}

func (s *Store) Path() string {
	return s.path
}

func (s *Store) Exists() (bool, error) {
	_, err := os.Stat(s.path)
	if err == nil {
		return true, nil
	}
	if errors.Is(err, fs.ErrNotExist) {
		return false, nil
	}
	return false, fmt.Errorf("stat state file: %w", err)
}

func (s *Store) Load() (*File, error) {
	data, err := os.ReadFile(s.path)
	if err != nil {
		return nil, fmt.Errorf("read state file: %w", err)
	}

	var st File
	if err := json.Unmarshal(data, &st); err != nil {
		return nil, fmt.Errorf("decode state file: %w", err)
	}
	if err := st.Validate(); err != nil {
		return nil, err
	}
	return &st, nil
}

func (s *Store) Save(st *File) error {
	if err := st.Validate(); err != nil {
		return err
	}
	if err := os.MkdirAll(s.dir, 0o755); err != nil {
		return fmt.Errorf("create state dir: %w", err)
	}

	data, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return fmt.Errorf("encode state file: %w", err)
	}
	data = append(data, '\n')

	if err := os.WriteFile(s.path, data, 0o600); err != nil {
		return fmt.Errorf("write state file: %w", err)
	}
	return nil
}

func (s *Store) LoadOrBootstrap(input BootstrapInput) (*File, bool, error) {
	exists, err := s.Exists()
	if err != nil {
		return nil, false, err
	}
	if exists {
		st, err := s.Load()
		if err != nil {
			return nil, false, err
		}
		return st, false, nil
	}

	st, err := NewBootstrapState(input)
	if err != nil {
		return nil, false, err
	}
	if err := s.Save(st); err != nil {
		return nil, false, err
	}
	return st, true, nil
}

func NewBootstrapState(input BootstrapInput) (*File, error) {
	serverURL := strings.TrimSpace(input.ServerURL)
	endpointID := strings.TrimSpace(input.EndpointID)
	token := strings.TrimSpace(input.EnrollmentToken)
	homeDir := strings.TrimSpace(input.HomeDir)

	var missing []string
	if serverURL == "" {
		missing = append(missing, "--server-url")
	}
	if endpointID == "" {
		missing = append(missing, "--endpoint-id")
	}
	if token == "" {
		missing = append(missing, "--enrollment-token")
	}
	if homeDir == "" {
		missing = append(missing, "--home-dir")
	}
	if len(missing) > 0 {
		return nil, fmt.Errorf("missing bootstrap flags: %s", strings.Join(missing, ", "))
	}

	st := &File{
		Schema:                      SchemaName,
		Version:                     SchemaVersion,
		ServerURL:                   serverURL,
		EndpointID:                  endpointID,
		TokenKind:                   TokenKindEnrollment,
		Token:                       token,
		HomeDir:                     homeDir,
		PendingTaskResults:          []spmapi.SyncTaskResult{},
		PendingActionPreviewResults: []spmapi.ResponseActionPreviewResult{},
	}
	return st, st.Validate()
}

func (s *File) Validate() error {
	if s == nil {
		return errors.New("state is required")
	}
	if s.Schema != SchemaName {
		return fmt.Errorf("unsupported state schema: %q", s.Schema)
	}
	if s.Version != SchemaVersion {
		return fmt.Errorf("unsupported state version: %d", s.Version)
	}
	if strings.TrimSpace(s.ServerURL) == "" {
		return errors.New("state server_url is required")
	}
	if strings.TrimSpace(s.EndpointID) == "" {
		return errors.New("state endpoint_id is required")
	}
	if strings.TrimSpace(s.Token) == "" {
		return errors.New("state token is required")
	}
	if strings.TrimSpace(s.HomeDir) == "" {
		return errors.New("state home_dir is required")
	}
	switch s.TokenKind {
	case TokenKindEnrollment, TokenKindEndpoint:
	default:
		return fmt.Errorf("unsupported token_kind: %q", s.TokenKind)
	}
	if s.PendingTaskResults == nil {
		s.PendingTaskResults = []spmapi.SyncTaskResult{}
	}
	if s.PendingActionPreviewResults == nil {
		s.PendingActionPreviewResults = []spmapi.ResponseActionPreviewResult{}
	}
	return nil
}
