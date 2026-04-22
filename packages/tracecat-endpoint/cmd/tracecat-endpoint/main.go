package main

import (
	"fmt"
	"os"
	"runtime/debug"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/version"
)

func main() {
	buildInfo, ok := debug.ReadBuildInfo()
	if ok && buildInfo.Main.Version != "" && buildInfo.Main.Version != "(devel)" {
		version.Version = buildInfo.Main.Version
	}

	if _, err := fmt.Fprintf(
		os.Stdout,
		"hello from tracecat-endpoint %s\n",
		version.Version,
	); err != nil {
		_, _ = fmt.Fprintf(os.Stderr, "write hello-world output: %v\n", err)
		os.Exit(1)
	}
}
