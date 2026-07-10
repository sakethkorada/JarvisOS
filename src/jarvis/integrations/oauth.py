"""OAuth authorization-code flow for local HTTP integrations."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jarvis.settings import OAuthProviderSettings
from jarvis.storage.auth import AuthStore, OAuthTokenRecord


BrowserOpener = Callable[[str], bool]
InputReader = Callable[[str], str]


@dataclass(frozen=True)
class OAuthCallbackResult:
    """Captured OAuth redirect result."""

    code: str | None = None
    state: str | None = None
    error: str | None = None


class OAuthManager:
    """Resolve, refresh, and obtain OAuth access tokens on demand."""

    def __init__(
        self,
        providers: tuple[OAuthProviderSettings, ...],
        auth_store: AuthStore,
        browser_opener: BrowserOpener | None = None,
        input_reader: InputReader | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        self._providers = {provider.name: provider for provider in providers}
        self._auth_store = auth_store
        self._browser_opener = browser_opener or webbrowser.open
        self._input_reader = input_reader or input
        self._timeout_seconds = timeout_seconds

    def access_token(
        self,
        provider_name: str,
        force_authorize: bool = False,
    ) -> str | None:
        """Return a valid access token, prompting OAuth if needed."""
        provider = self._providers.get(provider_name)
        if provider is None:
            return None
        record = self._auth_store.get_token(provider_name)
        if (
            record is not None
            and not force_authorize
            and not self._auth_store.token_is_expired(record)
        ):
            return record.access_token
        if record is not None and record.refresh_token and not force_authorize:
            refreshed = self.refresh(provider, record, suppress_errors=True)
            if refreshed is not None:
                return refreshed.access_token
        authorized = self.authorize(provider)
        return authorized.access_token

    def refresh(
        self,
        provider: OAuthProviderSettings,
        record: OAuthTokenRecord,
        suppress_errors: bool = False,
    ) -> OAuthTokenRecord | None:
        """Refresh an expired access token when a refresh token exists."""
        if provider.token_url is None or record.refresh_token is None:
            return None
        payload: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": record.refresh_token,
        }
        self._add_client_credentials(provider, payload)
        try:
            response = _post_form(provider.token_url, payload)
        except RuntimeError:
            if not suppress_errors:
                raise
            return None
        return self._store_token_response(provider.name, response, record.refresh_token)

    def authorize(self, provider: OAuthProviderSettings) -> OAuthTokenRecord:
        """Run a local authorization-code + PKCE flow."""
        self._validate_provider(provider)
        verifier = _code_verifier()
        state = secrets.token_urlsafe(24)
        with _LocalCallbackServer(provider.redirect_uri, self._timeout_seconds) as server:
            params = {
                "response_type": "code",
                "client_id": provider.client_id or "",
                "redirect_uri": server.redirect_uri,
                "scope": " ".join(provider.scopes),
                "state": state,
                "code_challenge": _code_challenge(verifier),
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent",
            }
            auth_url = f"{provider.authorization_url}?{urlencode(params)}"
            print("")
            print(f"Authorization required for {provider.name}.")
            print("Open this URL to continue:")
            print(auth_url)
            print("")
            print(
                "If the browser says authorization completed but JarvisOS does "
                "not continue, paste the final redirected URL or authorization "
                "code here and press Enter."
            )
            self._browser_opener(auth_url)
            callback = self._wait_for_callback_or_manual(server)
        if callback.error:
            raise RuntimeError(f"OAuth authorization failed: {callback.error}")
        if callback.code is None or (
            callback.state is not None and callback.state != state
        ):
            raise RuntimeError("OAuth authorization returned an invalid callback.")
        token_payload = {
            "grant_type": "authorization_code",
            "code": callback.code,
            "redirect_uri": provider.redirect_uri or "",
            "code_verifier": verifier,
        }
        self._add_client_credentials(provider, token_payload)
        response = _post_form(provider.token_url or "", token_payload)
        return self._store_token_response(provider.name, response)

    def _wait_for_callback_or_manual(
        self,
        server: "_LocalCallbackServer",
    ) -> OAuthCallbackResult:
        manual = _ManualCodeReader(self._input_reader)
        manual.start()
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            callback = server.poll()
            if callback is not None:
                return callback
            manual_result = manual.poll()
            if manual_result is not None:
                return manual_result
            time.sleep(0.25)
        raise RuntimeError(
            "Timed out waiting for OAuth authorization callback or pasted code."
        )

    def _store_token_response(
        self,
        provider_name: str,
        response: dict[str, Any],
        fallback_refresh_token: str | None = None,
    ) -> OAuthTokenRecord:
        access_token = str(response.get("access_token", "")).strip()
        if not access_token:
            raise RuntimeError("OAuth token response did not include access_token.")
        refresh_token = response.get("refresh_token") or fallback_refresh_token
        expires_at = _expires_at(response.get("expires_in"))
        return self._auth_store.set_token(
            provider_name,
            access_token,
            refresh_token=str(refresh_token) if refresh_token else None,
            expires_at=expires_at,
            preserve_refresh_token=True,
        )

    def _add_client_credentials(
        self,
        provider: OAuthProviderSettings,
        payload: dict[str, str],
    ) -> None:
        if provider.client_id:
            payload["client_id"] = provider.client_id
        secret = provider.client_secret()
        if secret:
            payload["client_secret"] = secret

    def _validate_provider(self, provider: OAuthProviderSettings) -> None:
        missing = []
        if not provider.client_id:
            missing.append("client_id")
        if not provider.authorization_url:
            missing.append("authorization_url")
        if not provider.token_url:
            missing.append("token_url")
        if not provider.redirect_uri:
            missing.append("redirect_uri")
        if missing:
            raise RuntimeError(
                f"OAuth provider {provider.name} is missing: {', '.join(missing)}"
            )


class _LocalCallbackServer:
    """Temporary localhost server that captures one OAuth redirect."""

    def __init__(self, redirect_uri: str | None, timeout_seconds: int) -> None:
        if redirect_uri is None:
            raise RuntimeError("OAuth redirect_uri is required.")
        parsed = urlparse(redirect_uri)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port
        if port is None:
            raise RuntimeError("OAuth redirect_uri must include a local port.")
        self.redirect_uri = redirect_uri
        self._path = parsed.path or "/"
        self._timeout_seconds = timeout_seconds
        self._event = Event()
        self._result = OAuthCallbackResult()
        self._server = ThreadingHTTPServer((host, port), self._handler())
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "_LocalCallbackServer":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def poll(self) -> OAuthCallbackResult | None:
        """Return the OAuth redirect result if it has arrived."""
        if self._event.is_set():
            return self._result
        return None

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        parent = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != parent._path:
                    self.send_error(404)
                    return
                values = parse_qs(parsed.query)
                parent._result = OAuthCallbackResult(
                    code=_first(values.get("code")),
                    state=_first(values.get("state")),
                    error=_first(values.get("error")),
                )
                parent._event.set()
                body = b"JarvisOS authorization complete. You can close this tab."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args) -> None:
                return

        return CallbackHandler


class _ManualCodeReader:
    """Background reader for pasted OAuth redirect URLs or codes."""

    def __init__(self, input_reader: InputReader) -> None:
        self._input_reader = input_reader
        self._event = Event()
        self._result: OAuthCallbackResult | None = None
        self._thread = Thread(target=self._read, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def poll(self) -> OAuthCallbackResult | None:
        if self._event.is_set():
            return self._result
        return None

    def _read(self) -> None:
        try:
            value = self._input_reader("> ").strip()
        except (EOFError, OSError):
            return
        if not value:
            return
        self._result = _callback_from_manual_input(value)
        self._event.set()


def _callback_from_manual_input(value: str) -> OAuthCallbackResult:
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        values = parse_qs(parsed.query)
        return OAuthCallbackResult(
            code=_first(values.get("code")),
            state=_first(values.get("state")),
            error=_first(values.get("error")),
        )
    return OAuthCallbackResult(code=value)


def _post_form(url: str, payload: dict[str, str]) -> dict[str, Any]:
    body = urlencode(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            decoded = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = _oauth_http_error_detail(exc)
        raise RuntimeError(detail) from exc
    except URLError as exc:
        raise RuntimeError(f"OAuth token request failed: {exc.reason}") from exc
    data = json.loads(decoded)
    if not isinstance(data, dict):
        raise RuntimeError("OAuth token response must be a JSON object.")
    return data


def _oauth_http_error_detail(error: HTTPError) -> str:
    """Return a redacted provider token-endpoint error."""
    body = error.read().decode("utf-8", errors="replace").strip()
    if body:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            code = str(payload.get("error", "")).strip()
            description = str(payload.get("error_description", "")).strip()
            if code and description:
                return (
                    f"OAuth token request failed with {error.code}: "
                    f"{code}: {description}"
                )
            if code:
                return f"OAuth token request failed with {error.code}: {code}"
        return f"OAuth token request failed with {error.code}: {body[:500]}"
    return f"OAuth token request failed with {error.code}."


def _code_verifier() -> str:
    return secrets.token_urlsafe(64)


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _expires_at(expires_in: Any) -> str | None:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    expires = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return expires.isoformat()


def _first(values: list[str] | None) -> str | None:
    if not values:
        return None
    return values[0]
