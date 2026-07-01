"""
MicroVM Executor Server — Runs inside the Lambda MicroVM.

Receives commands from the orchestrator Lambda via HTTP and executes them
in the isolated VM environment. Returns stdout/stderr/exit_code.

Endpoints:
- POST /exec     — Execute a shell command
- POST /clone    — Clone a repo (with branch)
- POST /scan     — Run a predefined scan step
- GET  /health   — Health check
- GET  /context  — Read .ai-review/ files from cloned repo
"""

import json
import os
import subprocess
import glob
from http.server import HTTPServer, BaseHTTPRequestHandler


WORKSPACE = "/workspace"
RESULTS = "/results"


class ExecutorHandler(BaseHTTPRequestHandler):
    """HTTP handler for executing commands in the MicroVM."""

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "running", "workspace": WORKSPACE})
        elif self.path == "/context":
            self._handle_context()
        else:
            self._respond(404, {"error": "Not found"})

    def do_POST(self):
        body = self._read_body()

        if self.path == "/exec":
            self._handle_exec(body)
        elif self.path == "/clone":
            self._handle_clone(body)
        elif self.path == "/scan":
            self._handle_scan(body)
        else:
            self._respond(404, {"error": "Not found"})

    def _handle_exec(self, body):
        """Execute an arbitrary shell command."""
        command = body.get("command", "")
        timeout = body.get("timeout", 120)
        cwd = body.get("cwd", WORKSPACE)

        if not command:
            self._respond(400, {"error": "command required"})
            return

        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=cwd
            )
            self._respond(200, {
                "exit_code": result.returncode,
                "stdout": result.stdout[-50000:],  # Truncate large output
                "stderr": result.stderr[-10000:],
            })
        except subprocess.TimeoutExpired:
            self._respond(408, {"error": f"Command timed out after {timeout}s"})
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _handle_clone(self, body):
        """Clone a Git repository."""
        url = body.get("url", "")
        branch = body.get("branch", "main")
        depth = body.get("depth", 1)

        if not url:
            self._respond(400, {"error": "url required"})
            return

        # Clean workspace
        subprocess.run("rm -rf /workspace/*", shell=True)

        # Clone
        cmd = f"git clone --branch {branch} --depth {depth} {url} /workspace/repo"
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                self._respond(200, {
                    "success": True,
                    "path": "/workspace/repo",
                    "branch": branch,
                })
            else:
                self._respond(500, {
                    "success": False,
                    "error": result.stderr,
                })
        except subprocess.TimeoutExpired:
            self._respond(408, {"error": "Clone timed out"})

    def _handle_scan(self, body):
        """Run a predefined scan step."""
        scan_type = body.get("type", "")
        target = body.get("target", "/workspace/repo")

        scans = {
            "bandit": f"bandit -r {target} -f json -ll 2>/dev/null || true",
            "pip-audit": f"cd {target} && pip-audit -f json 2>/dev/null || true",
            "ruff": f"ruff check {target} --output-format json 2>/dev/null || true",
            "safety": f"cd {target} && safety check --json 2>/dev/null || true",
            "npm-audit": f"cd {target} && npm audit --json 2>/dev/null || true",
            "gitleaks": f"gitleaks detect --source {target} --report-format json --report-path /results/gitleaks.json 2>/dev/null; cat /results/gitleaks.json 2>/dev/null || echo '[]'",
            "tfsec": f"tfsec {target} --format json --no-color 2>/dev/null || true",
            "checkov": f"checkov -d {target} --output json --quiet --compact 2>/dev/null || true",
            "cfn-lint": f"cfn-lint {target}/**/*.yaml --format json 2>/dev/null || true",
            "detect-secrets": f"cd {target} && detect-secrets scan --all-files 2>/dev/null || true",
            "dependency-check": f"dependency-check --project scan --scan {target} --format JSON --out /results/ 2>/dev/null; cat /results/dependency-check-report.json 2>/dev/null || echo '{{}}'",
        }

        if scan_type not in scans:
            self._respond(400, {"error": f"Unknown scan type: {scan_type}. Available: {list(scans.keys())}"})
            return

        cmd = scans[scan_type]
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=180
            )
            self._respond(200, {
                "scan_type": scan_type,
                "exit_code": result.returncode,
                "output": result.stdout[-50000:],
                "errors": result.stderr[-5000:] if result.returncode != 0 else "",
            })
        except subprocess.TimeoutExpired:
            self._respond(408, {"error": f"Scan {scan_type} timed out"})

    def _handle_context(self):
        """Read .ai-review/ context files from the cloned repo."""
        context_dir = "/workspace/repo/.ai-review"

        if not os.path.isdir(context_dir):
            self._respond(200, {"has_context": False, "files": [], "content": ""})
            return

        files = []
        content_parts = []

        for md_file in sorted(glob.glob(f"{context_dir}/**/*.md", recursive=True)):
            rel_path = md_file.replace(context_dir + "/", "")
            try:
                with open(md_file, "r", encoding="utf-8") as f:
                    text = f.read()
                files.append({"name": rel_path, "size": len(text)})
                content_parts.append(text)
            except Exception:
                pass

        self._respond(200, {
            "has_context": True,
            "files": files,
            "content": "\n\n---\n\n".join(content_parts),
        })

    def _read_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _respond(self, status: int, body: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), ExecutorHandler)
    print(f"MicroVM executor running on :{port}")
    server.serve_forever()
