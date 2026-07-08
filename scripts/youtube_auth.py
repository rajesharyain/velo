"""
One-time script to get a YouTube OAuth refresh token.

Usage:
  python scripts/youtube_auth.py

Then copy the printed YOUTUBE_REFRESH_TOKEN into your .env file.
"""

import json
import socket
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

CLIENT_ID = input("Paste your YOUTUBE_CLIENT_ID: ").strip()
CLIENT_SECRET = input("Paste your YOUTUBE_CLIENT_SECRET: ").strip()

SCOPE = "https://www.googleapis.com/auth/youtube.upload"

# Find a free port for the local callback server
with socket.socket() as s:
    s.bind(("localhost", 0))
    PORT = s.getsockname()[1]

REDIRECT = f"http://localhost:{PORT}"
_auth_code: list[str] = []


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = (params.get("code") or [""])[0]
        if code:
            _auth_code.append(code)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Authorization complete. You can close this tab.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h2>No code received. Please try again.</h2>")

    def log_message(self, *args: object) -> None:
        pass  # silence server logs


server = HTTPServer(("localhost", PORT), _Handler)
thread = threading.Thread(target=server.handle_request)
thread.daemon = True
thread.start()

auth_url = (
    "https://accounts.google.com/o/oauth2/auth?"
    + urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })
)

print(f"\nOpening browser for Google sign-in (callback on port {PORT})…")
webbrowser.open(auth_url)
print("Waiting for Google to redirect back…")

thread.join(timeout=120)
server.server_close()

if not _auth_code:
    print("ERROR: No authorisation code received within 2 minutes.")
    raise SystemExit(1)

code = _auth_code[0]

data = urllib.parse.urlencode({
    "code": code,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "redirect_uri": REDIRECT,
    "grant_type": "authorization_code",
}).encode()

req = urllib.request.Request(
    "https://oauth2.googleapis.com/token",
    data=data,
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    method="POST",
)
with urllib.request.urlopen(req) as resp:
    result = json.loads(resp.read())

refresh_token = result.get("refresh_token")
if not refresh_token:
    print("ERROR: no refresh_token in response:", result)
    raise SystemExit(1)

print("\n✓ Add these to your .env:\n")
print(f"YOUTUBE_CLIENT_ID={CLIENT_ID}")
print(f"YOUTUBE_CLIENT_SECRET={CLIENT_SECRET}")
print(f"YOUTUBE_REFRESH_TOKEN={refresh_token}")
