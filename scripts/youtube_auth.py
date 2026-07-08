"""
One-time script to get a YouTube OAuth refresh token.

Usage:
  python scripts/youtube_auth.py

Then copy the printed YOUTUBE_REFRESH_TOKEN into your .env file.
"""

import json
import urllib.parse
import urllib.request
import webbrowser

CLIENT_ID = input("Paste your YOUTUBE_CLIENT_ID: ").strip()
CLIENT_SECRET = input("Paste your YOUTUBE_CLIENT_SECRET: ").strip()

SCOPE = "https://www.googleapis.com/auth/youtube.upload"
REDIRECT = "urn:ietf:wg:oauth:2.0:oob"

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

print("\nOpening browser for Google sign-in…")
webbrowser.open(auth_url)
print(f"\nIf the browser didn't open, visit:\n{auth_url}\n")

code = input("Paste the authorisation code shown by Google: ").strip()

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
else:
    print("\n✓ Add these to your .env:\n")
    print(f"YOUTUBE_CLIENT_ID={CLIENT_ID}")
    print(f"YOUTUBE_CLIENT_SECRET={CLIENT_SECRET}")
    print(f"YOUTUBE_REFRESH_TOKEN={refresh_token}")
