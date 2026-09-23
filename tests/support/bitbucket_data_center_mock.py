"""Local Data Center REST + real Git smart-HTTPS development server.

Run with ``uv run python -m tests.support.bitbucket_data_center_mock --directory /tmp/bitbucket-dc``.
The synthetic development token is never a credential for a real service.
"""

import argparse
import json
import os
import ssl
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, unquote, urlsplit

TOKEN = "synthetic-data-center-dev-token"


class MockDataCenter(ThreadingHTTPServer):
    def __init__(self, directory: Path, port: int = 0):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.repository = directory / "DEMO" / "sync.git"
        self.prs: list[dict] = []
        self.fail_next_pr = False
        self.page_size = 2
        self.requests: list[tuple[str, str]] = []
        if not self.repository.exists():
            self.repository.parent.mkdir(exist_ok=True)
            subprocess.run(
                [
                    "git",
                    "init",
                    "--bare",
                    "--initial-branch=main",
                    str(self.repository),
                ],
                check=True,
                capture_output=True,
            )
            tree = self.git("mktree", data=b"").strip()
            commit = (
                self.git("commit-tree", tree.decode(), data=b"Initial commit")
                .decode()
                .strip()
            )
            self.git("update-ref", "refs/heads/main", commit)
            self.git("config", "http.receivepack", "true")
        self.cert = directory / "localhost.crt"
        key = directory / "localhost.key"
        if not self.cert.exists():
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(key),
                    "-out",
                    str(self.cert),
                    "-days",
                    "2",
                    "-subj",
                    "/CN=localhost",
                    "-addext",
                    "subjectAltName=DNS:localhost,DNS:host.docker.internal,IP:127.0.0.1",
                ],
                check=True,
                capture_output=True,
            )
            key.chmod(0o600)
        super().__init__(("127.0.0.1", port), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, key)
        self.socket = context.wrap_socket(self.socket, server_side=True)
        self.base_url = f"https://localhost:{self.server_port}/bitbucket"

    def git(self, *args: str, data: bytes | None = None) -> bytes:
        return subprocess.check_output(
            ["git", "--git-dir", str(self.repository), *args],
            input=data,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "QA",
                "GIT_AUTHOR_EMAIL": "qa@example.test",
                "GIT_COMMITTER_NAME": "QA",
                "GIT_COMMITTER_EMAIL": "qa@example.test",
            },
            stderr=subprocess.PIPE,
        )


class Handler(BaseHTTPRequestHandler):
    @property
    def dc(self) -> MockDataCenter:
        return cast(MockDataCenter, self.server)

    def log_message(self, format: str, *args: object):
        pass

    def json(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        self.dc.requests.append((self.command, path))
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.json({"errors": [{"message": "Unauthorized"}]}, 401)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if path.startswith("/bitbucket/scm/DEMO/sync.git/"):
            env = {
                **os.environ,
                "GIT_PROJECT_ROOT": str(self.dc.directory),
                "GIT_HTTP_EXPORT_ALL": "1",
                "PATH_INFO": path.removeprefix("/bitbucket/scm"),
                "QUERY_STRING": parsed.query,
                "REQUEST_METHOD": self.command,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(len(body)),
                "REMOTE_USER": "qa",
                "SERVER_PROTOCOL": "HTTP/1.1",
            }
            output = subprocess.check_output(
                ["git", "http-backend"], input=body, env=env
            )
            headers, content = output.split(b"\r\n\r\n", 1)
            pairs = [line.decode().split(":", 1) for line in headers.split(b"\r\n")]
            status = next(
                (int(v.strip().split()[0]) for k, v in pairs if k.lower() == "status"),
                200,
            )
            self.send_response(status)
            for k, v in pairs:
                if k.lower() != "status":
                    self.send_header(k, v.strip())
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        prefix = "/bitbucket/rest/api/1.0/projects/DEMO/repos/sync/"
        if not path.startswith(prefix):
            self.json({"errors": [{"message": "Not found"}]}, 404)
            return
        endpoint = path.removeprefix(prefix)
        query = parse_qs(parsed.query)
        values = []
        if endpoint == "default-branch":
            self.json({"id": "refs/heads/main", "displayId": "main", "isDefault": True})
            return
        if endpoint == "branches":
            names = (
                self.dc.git("for-each-ref", "--format=%(refname:short)", "refs/heads")
                .decode()
                .splitlines()
            )
            values = [
                {"id": f"refs/heads/{n}", "displayId": n, "isDefault": n == "main"}
                for n in names
                if query.get("filterText", [""])[0] in n
            ]
        elif endpoint == "commits":
            until = query.get("until", ["main"])[0]
            since = query.get("since", [None])[0]
            refs = [until] if since is None else [until, "--not", since]
            shas = self.dc.git("rev-list", *refs).decode().splitlines()
            for sha in shas:
                values.append(
                    {
                        "id": sha,
                        "message": self.dc.git(
                            "show", "-s", "--format=%B", sha
                        ).decode(),
                        "authorTimestamp": int(
                            self.dc.git("show", "-s", "--format=%at", sha)
                        )
                        * 1000,
                        "author": {"name": "QA"},
                    }
                )
        elif endpoint == "pull-requests":
            if self.command == "POST":
                if self.dc.fail_next_pr:
                    self.dc.fail_next_pr = False
                    self.json({"errors": [{"message": "Simulated outage"}]}, 503)
                    return
                request = json.loads(body)
                if not all(key in request for key in ("title", "fromRef", "toRef")):
                    self.json({"errors": [{"message": "Invalid PR shape"}]}, 400)
                    return
                pr = {**request, "id": len(self.dc.prs) + 1, "state": "OPEN"}
                self.dc.prs.append(pr)
                self.json(pr, 201)
                return
            values = [
                p
                for p in self.dc.prs
                if p["fromRef"]["id"] == query.get("at", [p["fromRef"]["id"]])[0]
            ]
        else:
            self.json({"errors": [{"message": "Not found"}]}, 404)
            return
        start = int(query.get("start", ["0"])[0])
        limit = min(int(query.get("limit", ["25"])[0]), self.dc.page_size)
        end = start + limit
        self.json(
            {
                "values": values[start:end],
                "start": start,
                "limit": limit,
                "size": len(values[start:end]),
                "isLastPage": end >= len(values),
                "nextPageStart": end,
            }
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args()
    server = MockDataCenter(args.directory, args.port)
    print(
        f"Instance: {server.base_url}\nRepository: git+ssh://git@localhost/DEMO/sync.git\nDevelopment token: {TOKEN}\nTrust CA with SSL_CERT_FILE={server.cert}",
        flush=True,
    )
    server.serve_forever()
