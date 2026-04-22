package state

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadOrBootstrapCreatesStateFile(t *testing.T) {
	root := t.TempDir()
	store := NewStore(filepath.Join(root, ".tracecatd"))

	st, created, err := store.LoadOrBootstrap(BootstrapInput{
		ServerURL:       "https://tracecat.example",
		EndpointID:      "endpoint-123",
		EnrollmentToken: "enroll-secret",
		HomeDir:         "/Users/example",
	})
	if err != nil {
		t.Fatalf("LoadOrBootstrap() error = %v", err)
	}
	if !created {
		t.Fatal("expected state to be created")
	}
	if st.TokenKind != TokenKindEnrollment {
		t.Fatalf("expected enrollment token kind, got %q", st.TokenKind)
	}

	info, err := os.Stat(store.Path())
	if err != nil {
		t.Fatalf("stat state file: %v", err)
	}
	if got := info.Mode().Perm(); got != 0o600 {
		t.Fatalf("unexpected permissions %o", got)
	}
}

func TestLoadOrBootstrapLoadsExistingState(t *testing.T) {
	root := t.TempDir()
	store := NewStore(filepath.Join(root, ".tracecatd"))

	initial, err := NewBootstrapState(BootstrapInput{
		ServerURL:       "https://tracecat.example",
		EndpointID:      "endpoint-123",
		EnrollmentToken: "enroll-secret",
		HomeDir:         "/Users/example",
	})
	if err != nil {
		t.Fatalf("NewBootstrapState() error = %v", err)
	}
	initial.TokenKind = TokenKindEndpoint
	initial.Token = "endpoint-secret"
	if err := store.Save(initial); err != nil {
		t.Fatalf("Save() error = %v", err)
	}

	st, created, err := store.LoadOrBootstrap(BootstrapInput{})
	if err != nil {
		t.Fatalf("LoadOrBootstrap() error = %v", err)
	}
	if created {
		t.Fatal("expected existing state to be reused")
	}
	if st.Token != "endpoint-secret" {
		t.Fatalf("unexpected token %q", st.Token)
	}
}

func TestNewBootstrapStateRequiresBootstrapFlags(t *testing.T) {
	_, err := NewBootstrapState(BootstrapInput{ServerURL: "https://tracecat.example"})
	if err == nil {
		t.Fatal("expected bootstrap validation error")
	}
}
