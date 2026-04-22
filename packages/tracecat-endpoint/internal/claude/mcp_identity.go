package claude

import (
	"net/url"
	"path/filepath"
	"strings"
)

type MCPIdentity struct {
	Resolved  string
	Transport string
	Evidence  map[string]any
}

func ResolveMCPIdentity(server map[string]any) MCPIdentity {
	if rawURL, ok := pickString(server, "url", "endpoint"); ok {
		normalized, origin := NormalizeURLIdentity(rawURL)
		return MCPIdentity{
			Resolved:  normalized,
			Transport: "http",
			Evidence: map[string]any{
				"url":             normalized,
				"origin":          origin,
				"resolved_url":    normalized,
				"resolved_origin": origin,
			},
		}
	}

	command, _ := pickString(server, "command", "cmd")
	args := stringSlice(server["args"])
	identity := NormalizeStdioIdentity(command, args)
	return MCPIdentity{
		Resolved:  identity,
		Transport: "stdio",
		Evidence: map[string]any{
			"command":          command,
			"args":             args,
			"resolved_command": identity,
		},
	}
}

func NormalizeURLIdentity(raw string) (string, string) {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		trimmed := strings.TrimSpace(raw)
		return trimmed, trimmed
	}
	parsed.Fragment = ""
	parsed.RawFragment = ""
	parsed.RawQuery = ""
	parsed.Host = strings.ToLower(parsed.Hostname())
	if port := parsed.Port(); port != "" && !isDefaultPort(parsed.Scheme, port) {
		parsed.Host = parsed.Host + ":" + port
	}
	cleanPath := filepath.Clean(parsed.EscapedPath())
	if cleanPath == "." || cleanPath == "/" {
		parsed.Path = ""
	} else {
		parsed.Path = cleanPath
	}
	origin := parsed.Scheme + "://" + parsed.Host
	if parsed.Path == "" {
		return origin, origin
	}
	return origin + parsed.Path, origin
}

func NormalizeStdioIdentity(command string, args []string) string {
	base := filepath.Base(strings.TrimSpace(command))
	packageManagers := map[string]bool{
		"npx":  true,
		"npm":  true,
		"pnpm": true,
		"yarn": true,
		"uvx":  true,
		"pipx": true,
	}
	if packageManagers[base] {
		for _, arg := range args {
			if strings.TrimSpace(arg) == "" || strings.HasPrefix(arg, "-") {
				continue
			}
			return "package:" + arg
		}
	}
	if base == "node" || base == "python" || base == "python3" {
		for _, arg := range args {
			if strings.TrimSpace(arg) == "" || strings.HasPrefix(arg, "-") {
				continue
			}
			return "binary:" + filepath.Base(arg)
		}
	}
	if base == "" {
		return "binary:unknown"
	}
	return "binary:" + base
}

func isDefaultPort(scheme string, port string) bool {
	return (scheme == "http" && port == "80") || (scheme == "https" && port == "443")
}

func pickString(doc map[string]any, keys ...string) (string, bool) {
	for _, key := range keys {
		if value, ok := stringValue(doc[key]); ok {
			return value, true
		}
	}
	return "", false
}

func stringValue(raw any) (string, bool) {
	value, ok := raw.(string)
	if !ok || strings.TrimSpace(value) == "" {
		return "", false
	}
	return strings.TrimSpace(value), true
}

func stringSlice(raw any) []string {
	switch value := raw.(type) {
	case []string:
		return value
	case []any:
		items := make([]string, 0, len(value))
		for _, item := range value {
			text, ok := stringValue(item)
			if ok {
				items = append(items, text)
			}
		}
		return items
	default:
		return nil
	}
}
