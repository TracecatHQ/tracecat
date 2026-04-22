package launchagent

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
)

const Label = "com.tracecat.tracecatd"

type Commander interface {
	Run(name string, args ...string) error
}

type execCommander struct{}

func (execCommander) Run(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		text := strings.TrimSpace(string(output))
		if text == "" {
			return fmt.Errorf("%s %v: %w", name, args, err)
		}
		return fmt.Errorf("%s %v: %s: %w", name, args, text, err)
	}
	return nil
}

type Installer struct {
	UserHome string
	UID      int
	Command  Commander
}

func NewInstaller(userHome string, uid int) *Installer {
	return &Installer{
		UserHome: userHome,
		UID:      uid,
		Command:  execCommander{},
	}
}

func (i *Installer) launchAgentsDir() string {
	return filepath.Join(i.UserHome, "Library", "LaunchAgents")
}

func (i *Installer) PlistPath() string {
	return filepath.Join(i.launchAgentsDir(), Label+".plist")
}

func (i *Installer) Install(binaryPath string, stateDir string) error {
	if strings.TrimSpace(binaryPath) == "" {
		return errors.New("binary path is required")
	}
	if strings.TrimSpace(stateDir) == "" {
		return errors.New("state dir is required")
	}
	if i.Command == nil {
		i.Command = execCommander{}
	}

	if err := os.MkdirAll(i.launchAgentsDir(), 0o755); err != nil {
		return fmt.Errorf("create LaunchAgents dir: %w", err)
	}
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		return fmt.Errorf("create state dir: %w", err)
	}

	plist, err := plist(Label, binaryPath, stateDir)
	if err != nil {
		return err
	}
	if err := os.WriteFile(i.PlistPath(), plist, 0o644); err != nil {
		return fmt.Errorf("write plist: %w", err)
	}

	uidTarget := "gui/" + strconv.Itoa(i.UID)
	if err := i.Command.Run("launchctl", "bootstrap", uidTarget, i.PlistPath()); err != nil {
		return fmt.Errorf("bootstrap launch agent: %w", err)
	}
	return nil
}

func (i *Installer) Uninstall() error {
	if i.Command == nil {
		i.Command = execCommander{}
	}
	plistPath := i.PlistPath()
	if _, err := os.Stat(plistPath); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return fmt.Errorf("stat plist: %w", err)
	}

	uidTarget := "gui/" + strconv.Itoa(i.UID)
	bootoutErr := i.Command.Run("launchctl", "bootout", uidTarget, plistPath)
	removeErr := os.Remove(plistPath)
	if removeErr != nil {
		return fmt.Errorf("remove plist: %w", removeErr)
	}
	if bootoutErr != nil {
		return fmt.Errorf("bootout launch agent: %w", bootoutErr)
	}
	return nil
}

func plist(label string, binaryPath string, stateDir string) ([]byte, error) {
	escaped := func(value string) string {
		var buf bytes.Buffer
		for _, r := range value {
			switch r {
			case '&':
				buf.WriteString("&amp;")
			case '<':
				buf.WriteString("&lt;")
			case '>':
				buf.WriteString("&gt;")
			case '"':
				buf.WriteString("&quot;")
			case '\'':
				buf.WriteString("&apos;")
			default:
				buf.WriteRune(r)
			}
		}
		return buf.String()
	}

	stdoutPath := filepath.Join(stateDir, "tracecatd.stdout.log")
	stderrPath := filepath.Join(stateDir, "tracecatd.stderr.log")
	content := fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>%s</string>
  <key>ProgramArguments</key>
  <array>
    <string>%s</string>
    <string>run</string>
    <string>--state-dir</string>
    <string>%s</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>%s</string>
  <key>StandardErrorPath</key>
  <string>%s</string>
</dict>
</plist>
`, escaped(label), escaped(binaryPath), escaped(stateDir), escaped(stdoutPath), escaped(stderrPath))
	return []byte(content), nil
}
