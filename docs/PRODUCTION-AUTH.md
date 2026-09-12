# Google sign-in on Netlify → Render

This is the current deployment recipe. It supersedes the earlier single-service static release recipe for these two sites. The browser still uses one origin; only hosting changes. No token-in-localStorage flow, third-party session cookie, extra service or new authentication provider is introduced.

## URLs and routing

- Public app: `https://stressdetect.netlify.app`
- API upstream: `https://studentstressdetector-backend.onrender.com`
- Google callback: **`https://stressdetect.netlify.app/api/auth/callback`**

The frontend `netlify.toml` places a forced `/api/*` HTTP 200 proxy rewrite **before** the SPA fallback. Its target is `https://studentstressdetector-backend.onrender.com/api/:splat`, retaining `/api`, all subsequent path segments, query parameters and HTTP methods. No fixed Origin header is injected: the real browser Origin must reach the backend for CSRF checking. The frontend Axios client already uses `/api` with credentials, and the Google link uses `/api/auth/login`. No frontend API URL or OAuth environment variable is required.

The backend allows exactly the configured frontend hostname and optional `BACKEND_HOST`. This permits Netlify upstream requests and Render's health probes without accepting arbitrary Host values. OAuth always builds its redirect URI from the configured `FRONTEND_ORIGIN`, never the request Host or forwarded-host headers. Authlib retains that URI for the code exchange. No broad forwarded-header trust is added.

Both the short-lived OIDC state cookie and the opaque application session cookie are Secure, HttpOnly, SameSite=Lax, Path=/, with no Domain attribute in production. Responses arrive at the browser through Netlify, so these are host-only Netlify cookies. Netlify must pass incoming Cookie and outgoing Set-Cookie headers, including multiple Set-Cookie values during callback/rotation. The browser sends the session automatically to same-origin `/api` calls. The Google callback is a top-level GET, allowing the Lax state cookie. Sessions remain hashed, expiring and revocable in PostgreSQL.

Mutation requests retain the exact Netlify Origin and the session's `X-CSRF-Token`. Logout revokes the database session and expires the same cookie. All backend `/api/` responses have `Cache-Control: no-store`; Netlify must not override this with shared caching. Security headers for frontend HTML are now set in `netlify.toml`, because that HTML no longer comes from FastAPI. Google/provider setup details never appear in the product UI.

## Owner setup: Google Cloud

1. Open [Google Cloud Console](https://console.cloud.google.com/) with the Google account that will own the application. Select or create its project.
2. Configure Google Auth Platform branding and audience (External for public Google-account access), including app name and support/developer contact details. Supply genuine homepage/privacy/domain information wherever the console requires it. Do not invent policy URLs. Configure testing access as applicable, and switch to production publishing when ready for public use; complete any verification Google requires for your branding/configuration.
3. Create an OAuth client of type **Web application**. Add the exact authorized redirect URI:
   `https://stressdetect.netlify.app/api/auth/callback`
4. This server-side authorization-code flow does not use the browser Google Identity Services SDK, so authorized JavaScript origins are not required by this implementation. If you choose to populate that field, use only `https://stressdetect.netlify.app`, without a path. The redirect URI above is mandatory.
5. Copy the client ID and client secret into Render's backend environment. Do not paste the secret into the frontend repository, Netlify variables or browser UI. No downloaded client JSON belongs in source control.
6. Local Google testing can use a separate web client with callback `http://localhost:5173/api/auth/callback`. The existing optional local development account needs no Google configuration.

The implementation requests `openid email profile` for the account name and verified email; no Gmail, Drive, Calendar, offline access or refresh-token scopes are needed. See Google's [web-server OAuth configuration](https://developers.google.com/identity/protocols/oauth2/web-server) and [OpenID Connect documentation](https://developers.google.com/identity/openid-connect/openid-connect).

## Owner setup: existing Render service

Deploy the updated backend repository to the **existing** `studentstressdetector-backend` service. Editing `render.yaml` locally does not update a dashboard-managed service by itself. Synchronize its settings manually unless it is managed by a Blueprint. Do not create a replacement service merely because the historical Blueprint display name differs.

| Environment variable | Required value |
| --- | --- |
| APP_ENV | `production` |
| ENABLE_DEV_AUTH | `false` |
| COOKIE_SECURE | `true` |
| FRONTEND_ORIGIN | `https://stressdetect.netlify.app` (no trailing slash) |
| BACKEND_HOST | `studentstressdetector-backend.onrender.com` (hostname only) |
| OIDC_CLIENT_ID | `<Google Web application's client ID>` |
| OIDC_CLIENT_SECRET | `<Google Web application's client secret>` |
| SESSION_SECRET | `<independent random secret, at least 32 characters>`; preserve an existing strong secret across restarts |
| DATABASE_URL | `<production PostgreSQL connection URL>`; preserve the existing production database |
| FRONTEND_DIST | Empty/unset; do not use the previous `static` value |
| PYTHON_VERSION | `3.12.14` as configured in the repository |

Leave unrelated AI environment values as they are. Do not configure TEST_DATABASE_URL on the production service. A session secret can be generated locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and entered directly into Render's environment settings.

Build command: `pip install -r requirements.txt`

Start command: `alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1 --limit-concurrency 64 --timeout-keep-alive 5 --no-access-log`

Health check: `/api/health/ready`

Clear the previous pre-deploy command when using this recipe. The configured free service cannot run Render pre-deploy commands, so migration now precedes application startup and a migration failure prevents startup. Keep one service instance; multi-instance migration orchestration is outside this setup. The build no longer requires staged frontend files. See [Render deploy lifecycle](https://render.com/docs/deploys).

## Owner setup: Netlify

Deploy the updated frontend repository to the existing `stressdetect` site using its `netlify.toml`: build `npm run build`, publish `dist`, Node 20.19+ or Node 22. The API rewrite must be present in the deployed configuration before the `/*` SPA rule. No OAuth credentials belong in Netlify. Remove obsolete VITE_API_URL/VITE_USE_LOCAL overrides if present; the current client does not use them.

The configured public origin is the production site. Preview/branch URLs are not alternate authenticated production origins: use production for the login check. Google and CSRF are deliberately bound to the exact configured origin.

## Verification after both deploys

1. Open `https://studentstressdetector-backend.onrender.com/api/health/ready`: expect ready/current schema. This also tests the Render host allowlist.
2. Open `https://stressdetect.netlify.app/api/auth/config`: expect JSON `{"oidc_enabled":true,"dev_login_enabled":false}`, never SPA HTML. Inspect `Cache-Control: no-store`.
3. From the production app click Continue with Google. The authorization request's redirect_uri must equal the callback above. Confirm a Secure/HttpOnly/Lax, host-only `oidc_state` cookie on the Netlify host.
4. Complete Google sign-in. The callback must return to the Netlify app, remove the temporary state cookie and set `wellbeing_session` on the Netlify host. It must not leave Google/provider tokens in the URL or browser storage.
5. Refresh and open Timeline: `/api/me` and authenticated reads must retain the session. Test a deliberate reversible preference change to check a real mutation; verify the request uses the Netlify Origin and X-CSRF-Token. Never add synthetic check-ins to a real user's history for a smoke test.
6. Sign out: logout returns 204, the session cookie expires, and `/api/me` returns 401. POST `/api/auth/dev-login` remains 404. Missing/wrong CSRF tokens or foreign Origins must receive 403 when a session exists.

Local tests emulate the proxy's Host/TLS boundary and use real PostgreSQL with a mocked Google HTTP provider and real signed-ID-token validation. They verify callback consistency in both authorization and token exchange, cookies, session persistence, authenticated APIs, rejection of invalid CSRF/Origin, logout, spoofed forwarded hosts and blocked development authentication. This is not a substitute for the hosted header/cookie and Google-account checks above.

At inspection on 2026-09-12, the live Netlify `/api/auth/config` returned HTML (200), and Render's same path returned JSON 404. The deployed versions therefore did not yet expose this implementation. No live configuration or secret was changed, and no authenticated Google production flow was claimed verified.

Verification on 2026-09-12: full backend suite **183 passed** (three existing dependency deprecation warnings), full frontend suite **32 passed**, ESLint and TypeScript/Vite production build passed. The new proxy-boundary integration includes real signed token validation and production cookie/logout/CSRF checks. Netlify TOML parsed successfully with the API rule first. Built JavaScript contains neither the development-account label nor the former OAuth setup instructions or hardcoded Render URL. Local auth regression tests remain green. These results do not claim that the two hosted sites have been redeployed.

## Hosting limits

Netlify proxy rewrites have a [26-second timeout](https://docs.netlify.com/manage/routing/redirects/rewrites-proxies/). The current Render Blueprint selects the free plan, whose [idle spin-down and roughly one-minute wake-up](https://render.com/docs/free) can exceed that timeout and the frontend's 12-second request timeout. A sleeping backend may require a later retry. This configuration does not upgrade hosting, introduce keep-alive infrastructure or promise uninterrupted availability. A production database must be durable; Render's free PostgreSQL offerings have their own expiry limits.

The Netlify proxy participates in the session security boundary. Google credentials, public publishing/verification and actual deployed header behavior require owner configuration and live validation. LLM functionality is unrelated to signing in and was not changed.

## Account profiles

Run `alembic upgrade head` before starting this release (revision `0004_account_profile`). This adds nullable display_name and google_email columns without changing existing identities or check-ins. Existing Google users sign out and sign in again to populate verified profile claims. Configure the Google consent screen for the basic email and profile scopes; callback and secrets remain unchanged.

`GET /api/me` includes display_name and google_email. `PATCH /api/account` accepts only a display_name (trimmed, 1–80 characters, no control/bidirectional override characters), protected by the existing session, Origin and CSRF checks. An edited name survives subsequent Google logins. Google email is read-only, refreshed from verified ID-token claims, and is never used to link accounts. Profile edits do not change assessment history versions. Exports include the profile; account deletion removes it with the user row and existing cascading data/session deletion. Profile details are not added to LLM fact bundles.

## Product simplification (2026-09-13)

Optional AI reflections and predictive-ML controls are removed from the product. The application no longer mounts `/api/ai/*` or `/api/ml/status`, so existing consent cannot trigger new provider calls. Retired engine code is retained only for historical regression coverage; stored reflections remain available through account exports and deletion. Core fuzzy-rule explanations remain active.

New check-ins and scenarios submit displayed slider defaults (sleep 6h, screen time 8h, other ratings 5/10) unless adjusted. No slider interaction is required and no slider change saves history. Strain remains separate from fuzzy inputs. Editing a legacy record with missing inputs displays defaults for those fields; only an explicit revision save records them. Previously stored missing values stay missing until then. Timezone remains a dropdown because dates, day boundaries and retrospective checks depend on it.
