package runner

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/user"
	"runtime"
	"strings"
	"time"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/inventory"
	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/spmapi"
	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/state"
	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/tasks"
	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/version"
)

const DefaultInterval = 5 * time.Minute

type Options struct {
	ServerURL       string
	StateDir        string
	HomeDir         string
	EndpointID      string
	EnrollmentToken string
	Interval        time.Duration
	HTTPClient      *http.Client
	Inventory       inventory.Provider
	Executor        tasks.Executor
	Stdout          io.Writer
	Stderr          io.Writer
}

type Service struct {
	store      *state.Store
	httpClient *http.Client
	inventory  inventory.Provider
	executor   tasks.Executor
	interval   time.Duration
	stdout     io.Writer
	stderr     io.Writer

	serverURL       string
	homeDir         string
	endpointID      string
	enrollmentToken string
}

func New(opts Options) (*Service, error) {
	userHome := strings.TrimSpace(opts.HomeDir)
	if userHome == "" {
		currentUser, err := user.Current()
		if err != nil {
			return nil, fmt.Errorf("resolve current user: %w", err)
		}
		userHome = currentUser.HomeDir
	}

	stateDir := strings.TrimSpace(opts.StateDir)
	if stateDir == "" {
		stateDir = state.DefaultDir(userHome)
	}
	if opts.Interval <= 0 {
		opts.Interval = DefaultInterval
	}
	if opts.Inventory == nil {
		opts.Inventory = inventory.NewClaudeProvider(userHome)
	}
	if opts.Executor == nil {
		opts.Executor = tasks.NewClaudeExecutor(userHome)
	}
	if opts.Stdout == nil {
		opts.Stdout = io.Discard
	}
	if opts.Stderr == nil {
		opts.Stderr = io.Discard
	}

	return &Service{
		store:           state.NewStore(stateDir),
		httpClient:      opts.HTTPClient,
		inventory:       opts.Inventory,
		executor:        opts.Executor,
		interval:        opts.Interval,
		stdout:          opts.Stdout,
		stderr:          opts.Stderr,
		serverURL:       opts.ServerURL,
		homeDir:         userHome,
		endpointID:      opts.EndpointID,
		enrollmentToken: opts.EnrollmentToken,
	}, nil
}

func (s *Service) StateDir() string {
	return s.store.Dir()
}

func (s *Service) EnsureState() (*state.File, error) {
	st, _, err := s.store.LoadOrBootstrap(state.BootstrapInput{
		ServerURL:       s.serverURL,
		EndpointID:      s.endpointID,
		EnrollmentToken: s.enrollmentToken,
		HomeDir:         s.homeDir,
	})
	if err != nil {
		return nil, err
	}
	return st, nil
}

func (s *Service) RunOnce(ctx context.Context) error {
	_, err := s.runCycle(ctx)
	return err
}

func (s *Service) Run(ctx context.Context) error {
	if _, err := s.runCycle(ctx); err != nil {
		s.logf(s.stderr, "sync cycle failed: %v", err)
	}

	ticker := time.NewTicker(s.interval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			if _, err := s.runCycle(ctx); err != nil {
				s.logf(s.stderr, "sync cycle failed: %v", err)
			}
		}
	}
}

func (s *Service) runCycle(ctx context.Context) (*state.File, error) {
	st, err := s.EnsureState()
	if err != nil {
		return nil, err
	}

	client, err := spmapi.NewClient(st.ServerURL, s.httpClient)
	if err != nil {
		return nil, err
	}

	snapshot, err := s.inventory.Collect(ctx)
	if err != nil {
		return nil, fmt.Errorf("collect inventory: %w", err)
	}

	metadata, err := endpointMetadata(st.HomeDir, s.interval)
	if err != nil {
		return nil, err
	}
	request := spmapi.SyncRequest{
		Name:            metadata.Name,
		EndpointVersion: metadata.EndpointVersion,
		Hostname:        metadata.Hostname,
		OSUser:          metadata.OSUser,
		HomePath:        metadata.HomePath,
		Status:          spmapi.EndpointStatusActive,
		ClientMetadata:  metadata.ClientMetadata,
		InventoryItems:  snapshot.InventoryItems,
		Relationships:   snapshot.Relationships,
		TaskResults:     cloneTaskResults(st.PendingTaskResults),
	}

	firstResponse, err := client.SyncEndpoint(ctx, st.EndpointID, st.Token, request)
	if err != nil {
		return nil, err
	}
	if firstResponse.EndpointSecret != "" {
		st.TokenKind = state.TokenKindEndpoint
		st.Token = firstResponse.EndpointSecret
	}
	st.PendingTaskResults = []spmapi.SyncTaskResult{}
	if err := s.store.Save(st); err != nil {
		return nil, err
	}

	results, err := s.executor.Execute(ctx, firstResponse.Tasks)
	if err != nil {
		return nil, fmt.Errorf("execute enforcement tasks: %w", err)
	}
	if len(results) == 0 {
		s.logf(s.stdout, "sync complete for endpoint %s", st.EndpointID)
		return st, nil
	}

	st.PendingTaskResults = cloneTaskResults(results)
	if err := s.store.Save(st); err != nil {
		return nil, err
	}

	request.TaskResults = cloneTaskResults(st.PendingTaskResults)
	secondResponse, err := client.SyncEndpoint(ctx, st.EndpointID, st.Token, request)
	if err != nil {
		return nil, err
	}
	if secondResponse.EndpointSecret != "" {
		st.TokenKind = state.TokenKindEndpoint
		st.Token = secondResponse.EndpointSecret
	}
	st.PendingTaskResults = []spmapi.SyncTaskResult{}
	if err := s.store.Save(st); err != nil {
		return nil, err
	}
	s.logf(s.stdout, "sync complete for endpoint %s", st.EndpointID)
	return st, nil
}

type Metadata struct {
	Name            string
	EndpointVersion string
	Hostname        string
	OSUser          string
	HomePath        string
	ClientMetadata  map[string]any
}

func endpointMetadata(homeDir string, interval time.Duration) (*Metadata, error) {
	hostname, err := os.Hostname()
	if err != nil {
		return nil, fmt.Errorf("resolve hostname: %w", err)
	}
	currentUser, err := user.Current()
	if err != nil {
		return nil, fmt.Errorf("resolve current user: %w", err)
	}
	name := strings.TrimSpace(os.Getenv("TRACECAT_DEVICE_NAME"))
	if name == "" {
		name = hostname
	}
	clientMetadata := map[string]any{
		"binary_name":           "tracecatd",
		"goos":                  runtime.GOOS,
		"goarch":                runtime.GOARCH,
		"poll_interval_seconds": int(interval.Seconds()),
	}
	if previewStack := strings.TrimSpace(os.Getenv("TRACECAT_PREVIEW_STACK")); previewStack != "" {
		clientMetadata["preview_stack"] = previewStack
	}
	if previewScenario := strings.TrimSpace(os.Getenv("TRACECAT_PREVIEW_SCENARIO")); previewScenario != "" {
		clientMetadata["preview_scenario"] = previewScenario
	}
	return &Metadata{
		Name:            name,
		EndpointVersion: version.Version,
		Hostname:        hostname,
		OSUser:          currentUser.Username,
		HomePath:        homeDir,
		ClientMetadata:  clientMetadata,
	}, nil
}

func cloneTaskResults(results []spmapi.SyncTaskResult) []spmapi.SyncTaskResult {
	if len(results) == 0 {
		return []spmapi.SyncTaskResult{}
	}
	cloned := make([]spmapi.SyncTaskResult, 0, len(results))
	for _, result := range results {
		item := result
		if result.Result != nil {
			item.Result = make(map[string]any, len(result.Result))
			for key, value := range result.Result {
				item.Result[key] = value
			}
		}
		cloned = append(cloned, item)
	}
	return cloned
}

func (s *Service) logf(writer io.Writer, format string, args ...any) {
	if writer == nil {
		return
	}
	_, _ = fmt.Fprintf(writer, format+"\n", args...)
}
