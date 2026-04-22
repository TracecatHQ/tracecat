package cli

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/user"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/launchagent"
	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/runner"
)

type App struct {
	stdout io.Writer
	stderr io.Writer
}

func NewApp(stdout io.Writer, stderr io.Writer) *App {
	return &App{stdout: stdout, stderr: stderr}
}

func (a *App) Run(ctx context.Context, args []string) error {
	if len(args) == 0 {
		a.usage()
		return errors.New("command is required")
	}

	switch args[0] {
	case "run":
		return a.runCommand(ctx, args[1:])
	case "install":
		return a.installCommand(args[1:])
	case "uninstall":
		return a.uninstallCommand()
	case "-h", "--help", "help":
		a.usage()
		return nil
	default:
		a.usage()
		return fmt.Errorf("unknown command %q", args[0])
	}
}

func (a *App) runCommand(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	fs.SetOutput(a.stderr)

	serverURL := fs.String("server-url", "", "Tracecat server base URL")
	stateDir := fs.String("state-dir", "", "tracecatd state directory")
	homeDir := fs.String("home-dir", "", "user home directory to inventory and manage")
	endpointID := fs.String("endpoint-id", "", "SPM endpoint ID")
	enrollmentToken := fs.String("enrollment-token", "", "one-time endpoint enrollment token")
	runOnce := fs.Bool("once", false, "run a single sync cycle and exit")

	if err := fs.Parse(args); err != nil {
		return err
	}

	service, err := runner.New(runner.Options{
		ServerURL:       *serverURL,
		StateDir:        *stateDir,
		HomeDir:         *homeDir,
		EndpointID:      *endpointID,
		EnrollmentToken: *enrollmentToken,
		Stdout:          a.stdout,
		Stderr:          a.stderr,
	})
	if err != nil {
		return err
	}

	if *runOnce {
		return service.RunOnce(ctx)
	}
	return service.Run(ctx)
}

func (a *App) installCommand(args []string) error {
	fs := flag.NewFlagSet("install", flag.ContinueOnError)
	fs.SetOutput(a.stderr)

	serverURL := fs.String("server-url", "", "Tracecat server base URL")
	stateDir := fs.String("state-dir", "", "tracecatd state directory")
	homeDir := fs.String("home-dir", "", "user home directory to inventory and manage")
	endpointID := fs.String("endpoint-id", "", "SPM endpoint ID")
	enrollmentToken := fs.String("enrollment-token", "", "one-time endpoint enrollment token")

	if err := fs.Parse(args); err != nil {
		return err
	}

	service, err := runner.New(runner.Options{
		ServerURL:       *serverURL,
		StateDir:        *stateDir,
		HomeDir:         *homeDir,
		EndpointID:      *endpointID,
		EnrollmentToken: *enrollmentToken,
		Stdout:          a.stdout,
		Stderr:          a.stderr,
	})
	if err != nil {
		return err
	}

	if _, err := service.EnsureState(); err != nil {
		return fmt.Errorf("initialize endpoint state: %w", err)
	}

	executable, err := os.Executable()
	if err != nil {
		return fmt.Errorf("resolve current executable: %w", err)
	}
	currentUser, err := user.Current()
	if err != nil {
		return fmt.Errorf("resolve current user: %w", err)
	}

	installer := launchagent.NewInstaller(currentUser.HomeDir, os.Getuid())
	if err := installer.Install(executable, service.StateDir()); err != nil {
		return err
	}

	_, _ = fmt.Fprintf(a.stdout, "installed LaunchAgent at %s\n", installer.PlistPath())
	return nil
}

func (a *App) uninstallCommand() error {
	currentUser, err := user.Current()
	if err != nil {
		return fmt.Errorf("resolve current user: %w", err)
	}

	installer := launchagent.NewInstaller(currentUser.HomeDir, os.Getuid())
	if err := installer.Uninstall(); err != nil {
		return err
	}

	_, _ = fmt.Fprintf(a.stdout, "removed LaunchAgent %s\n", installer.PlistPath())
	return nil
}

func (a *App) usage() {
	_, _ = fmt.Fprintln(a.stderr, "usage: tracecatd <run|install|uninstall> [flags]")
	_, _ = fmt.Fprintln(a.stderr, "  tracecatd run [--once] [--server-url ... --state-dir ... --home-dir ... --endpoint-id ... --enrollment-token ...]")
	_, _ = fmt.Fprintln(a.stderr, "  tracecatd install [--server-url ... --state-dir ... --home-dir ... --endpoint-id ... --enrollment-token ...]")
	_, _ = fmt.Fprintln(a.stderr, "  tracecatd uninstall")
}
