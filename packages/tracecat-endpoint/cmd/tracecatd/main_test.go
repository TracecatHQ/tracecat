package main

import (
	"testing"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/version"
)

func TestApplyBuildVersionKeepsDefaultForUntagedBuild(t *testing.T) {
	prev := version.Version
	t.Cleanup(func() {
		version.Version = prev
	})

	version.Version = "test"
	applyBuildVersion()

	if version.Version == "" {
		t.Fatal("expected version to remain set")
	}
}
