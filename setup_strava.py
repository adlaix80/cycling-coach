"""
setup_strava.py
One-time Strava OAuth2 flow.
Run this once to get your refresh token, which is then saved to .env.

Usage: python setup_strava.py
"""

import os
import webbrowser
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv, set_key

load_dotenv()

CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8080/callback"
SCOPE = "read,activity:read_all"
AUTH_URL = (
    f"https://www.strava.com/oauth/authorize"
    f"?client_id={CLIENT_ID}"
    f"&response_type=code"
    f"&redirect_uri={REDIRECT_URI}"
    f"&approval_prompt=force"
    f"&scope={SCOPE}"
)

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Strava authorisation successful! You can close this window.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h2>Authorisation failed. Please try again.</h2>")

    def log_message(self, format, *args):
        pass  # Suppress server logs


def main():
    print("=" * 60)
    print("Strava OAuth2 Setup")
    print("=" * 60)
    print(f"\nOpening Strava authorisation page...")
    webbrowser.open(AUTH_URL)
    print("If the browser didn't open, visit this URL manually:")
    print(f"\n{AUTH_URL}\n")

    print("Waiting for callback on http://localhost:8080 ...")
    server = HTTPServer(("localhost", 8080), CallbackHandler)
    server.handle_request()

    if not auth_code:
        print("❌ Failed to get authorisation code.")
        return

    print(f"✅ Got auth code. Exchanging for tokens...")
    response = requests.post("https://www.strava.com/oauth/token", data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": auth_code,
        "grant_type": "authorization_code",
    })
    response.raise_for_status()
    tokens = response.json()

    refresh_token = tokens["refresh_token"]
    access_token = tokens["access_token"]
    athlete = tokens.get("athlete", {})

    print(f"\n✅ Authorised as: {athlete.get('firstname')} {athlete.get('lastname')}")
    print(f"Refresh token: {refresh_token[:20]}...")

    # Save to .env
    env_path = ".env"
    if not os.path.exists(env_path):
        # Create from example
        import shutil
        if os.path.exists(".env.example"):
            shutil.copy(".env.example", env_path)

    set_key(env_path, "STRAVA_REFRESH_TOKEN", refresh_token)
    print(f"\n✅ Refresh token saved to {env_path}")
    print("\nStrava setup complete! You can now run: python main.py")


if __name__ == "__main__":
    main()
