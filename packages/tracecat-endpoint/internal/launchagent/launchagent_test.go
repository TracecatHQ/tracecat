package launchagent

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type recordingCommander struct {
	calls [][]string
	err   error
}

func (r *recordingCommander) Run(name string, args ...string) error {
	call := append([]string{name}, args...)
	r.calls = append(r.calls, call)
	return r.err
}

func TestInstallWritesPlistAndBootstraps(t *testing.T) {
	root := t.TempDir()
	homeDir := filepath.Join(root, "home")
	stateDir := filepath.Join(homeDir, ".tracecatd")
	binaryPath := filepath.Join(root, "tracecatd")
	if err := os.MkdirAll(homeDir, 0o755); err != nil {
		t.Fatalf("mkdir home: %v", err)
	}

	commander := &recordingCommander{}
	installer := NewInstaller(homeDir, 501)
	installer.Command = commander

	if err := installer.Install(binaryPath, stateDir); err != nil {
		t.Fatalf("Install() error = %v", err)
	}
	if len(commander.calls) != 1 {
		t.Fatalf("expected one command call, got %d", len(commander.calls))
	}
	gotCall := strings.Join(commander.calls[0], " ")
	if !strings.Contains(gotCall, "launchctl bootstrap gui/501") {
		t.Fatalf("unexpected command call %q", gotCall)
	}

	data, err := os.ReadFile(installer.PlistPath())
	if err != nil {
		t.Fatalf("read plist: %v", err)
	}
	text := string(data)
	if !strings.Contains(text, "<string>run</string>") {
		t.Fatalf("plist missing run command: %s", text)
	}
	if !strings.Contains(text, "<string>"+stateDir+"</string>") {
		t.Fatalf("plist missing state dir: %s", text)
	}
}

func TestUninstallBootsOutAndRemovesPlist(t *testing.T) {
	root := t.TempDir()
	homeDir := filepath.Join(root, "home")
	if err := os.MkdirAll(filepath.Join(homeDir, "Library", "LaunchAgents"), 0o755); err != nil {
		t.Fatalf("mkdir LaunchAgents: %v", err)
	}

	installer := NewInstaller(homeDir, 501)
	commander := &recordingCommander{}
	installer.Command = commander
	if err := os.WriteFile(installer.PlistPath(), []byte("plist"), 0o644); err != nil {
		t.Fatalf("write plist: %v", err)
	}

	if err := installer.Uninstall(); err != nil {
		t.Fatalf("Uninstall() error = %v", err)
	}
	if len(commander.calls) != 1 {
		t.Fatalf("expected one command call, got %d", len(commander.calls))
	}
	if _, err := os.Stat(installer.PlistPath()); !os.IsNotExist(err) {
		t.Fatalf("expected plist to be removed, stat error = %v", err)
	}
}
