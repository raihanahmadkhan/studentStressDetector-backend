# Deployment configuration

The frontend uses relative /api requests, which Netlify forwards to Render. Google returns to the frontend's /api/auth/callback path, also proxied to the backend.

Render supplies DATABASE_URL, SESSION_SECRET, OIDC_CLIENT_ID, and OIDC_CLIENT_SECRET. Use the project's current dedicated database, never a URL from another project or an old deployment. Credentials do not belong in Git or frontend configuration. Configure PostgreSQL TLS and provider certificate verification.

APP_ENV=production, ENABLE_DEV_AUTH=false, COOKIE_SECURE=true, an HTTPS FRONTEND_ORIGIN, and the exact BACKEND_HOST are required. The Google web client's redirect URI must match FRONTEND_ORIGIN plus /api/auth/callback.

render.yaml migrates before starting one API worker and checks readiness. Dashboard overrides must match it. Process startup alone does not verify database or authentication readiness.

Never run test reset/restore helpers against application data. Do not publish credentials, database dumps, local runtime directories, or access logs containing OAuth query parameters.
