# Mini App — early prototype (design history)

Early standalone prototype of the Mini App, built with React + Babel-standalone loaded
straight in the browser, before the current Vite/React build under `miniapp/frontend/`.

Kept as design history — the animations, day/night vibe, and layout were prototyped here
first. **Not wired to runtime or the build**: nothing in `miniapp/frontend/` (Vite) or
`miniapp/backend/` (FastAPI) imports these files.

- `Nexus Arcana.html`, `screens.jsx`, `atmosphere.jsx`, `styles.css`, `data.js`,
  `tweaks-panel.jsx` — the standalone prototype (HTML entry + components).
- `frontend-seed/`, `uploads/` — original concept art and the monolithic prototype
  source (`App.jsx`) used as reference while building the real app.
- `debug/` — fog/atmosphere render debug captures.
