# MarketLens web frontend (Next.js)

The web app for MarketLens. It shows the pages; the Python backend API (`python -m marketlens.api`)
does all the work. See the **Frontend** section of `docs/GUIDE.md` (in the project folder) for setup.

```bash
npm install        # once, and after package.json changes
npm run dev        # http://localhost:3000 (the Python API must run too)
npm run lint       # check the code
npm run build      # production build
```

| Where | What |
| --- | --- |
| `app/` | One folder per page (`app/settings/page.jsx` = /settings); `layout.jsx` wraps every page |
| `components/` | Shared pieces: `AppShell` (menu + top bar), `ComingSoon`, `SettingsStatus` |
| `lib/modules.js` | The menu and Home cards: every module's address, description and status |
| `lib/api.js` | `apiGet` / `apiPost`: talk to the Python API with readable error messages |
| `next.config.mjs` | Forwards `/api/...` and `/webhook/...` to `BACKEND_URL` |
| `.env.local.example` | Copy to `.env.local` to change `BACKEND_URL` |
