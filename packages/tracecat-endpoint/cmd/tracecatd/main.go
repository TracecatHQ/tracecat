package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"runtime/debug"
	"syscall"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/cli"
	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/version"
)

func main() {
	applyBuildVersion()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	app := cli.NewApp(os.Stdout, os.Stderr)
	if err := app.Run(ctx, os.Args[1:]); err != nil {
		_, _ = fmt.Fprintf(os.Stderr, "tracecatd: %v\n", err)
		os.Exit(1)
	}
}

func applyBuildVersion() {
	buildInfo, ok := debug.ReadBuildInfo()
	if !ok {
		return
	}
	if buildInfo.Main.Version != "" && buildInfo.Main.Version != "(devel)" {
		version.Version = buildInfo.Main.Version
	}
}
