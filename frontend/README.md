# Career-trans frontend

Requires Node.js 24.15+ and npm 11+. Copy `.env.example` to `.env.local` and set `VITE_API_BASE_URL` to the backend origin. The V1 client stores its bearer token in `sessionStorage` only; this is a development limitation, not the intended production authentication architecture.

Run `npm ci`, `npm run test`, `npm run typecheck`, and `npm run build` from this directory.
