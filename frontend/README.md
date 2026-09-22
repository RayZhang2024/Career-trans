# Career-trans frontend

Requires Node.js 24.15+ and npm 11+. By default the Vite development server
proxies same-origin `/api/*` requests to the local backend. Leave
`VITE_API_BASE_URL` unset for that default, or set it at build time only when
deliberately targeting a separately hosted API. The Docker Compose production
build also uses same-origin `/api/*`, served by nginx and proxied internally to
the backend container. The V1 client stores its bearer token in
`sessionStorage` only; this is a development limitation, not the intended
production authentication architecture.

Run `npm ci`, `npm run test`, `npm run typecheck`, and `npm run build` from this directory.
