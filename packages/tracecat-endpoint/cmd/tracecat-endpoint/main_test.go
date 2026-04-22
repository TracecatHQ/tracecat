package main

import (
	"bytes"
	"os"
	"strings"
	"testing"

	"github.com/TracecatHQ/tracecat/packages/tracecat-endpoint/internal/version"
)

func TestMainWritesHelloWorld(t *testing.T) {
	prevStdout := os.Stdout
	prevVersion := version.Version

	readPipe, writePipe, err := os.Pipe()
	if err != nil {
		t.Fatalf("create stdout pipe: %v", err)
	}

	t.Cleanup(func() {
		os.Stdout = prevStdout
		version.Version = prevVersion
		if writePipe != nil {
			_ = writePipe.Close()
		}
		if readPipe != nil {
			_ = readPipe.Close()
		}
	})

	version.Version = "test"
	os.Stdout = writePipe

	main()

	if err := writePipe.Close(); err != nil {
		t.Fatalf("close write pipe: %v", err)
	}
	writePipe = nil

	var output bytes.Buffer
	if _, err := output.ReadFrom(readPipe); err != nil {
		t.Fatalf("read stdout: %v", err)
	}
	if err := readPipe.Close(); err != nil {
		t.Fatalf("close read pipe: %v", err)
	}
	readPipe = nil

	if got := output.String(); !strings.Contains(got, "hello from tracecat-endpoint") {
		t.Fatalf("unexpected output %q", got)
	}
}
