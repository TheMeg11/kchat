# Admin panel source and build

The original repository shipped only a minified React bundle embedded in HTML;
no original JSX/TypeScript source or upstream project was supplied. `panel.js`
is the recovered, formatted bundle, including its vendor code. It is **not**
claimed to be the original application source. This keeps existing routes and
visual design intact while fixing the API client, server-side user search,
permission-dependent loading, restore upload and truthful monitoring.

Edit files in this directory, then run `npm ci && npm run build:panel`.
The reproducible production files in `webadmin/` are checked in, so Railway
needs Python only (no Node runtime/build service). Gzip assets are precompressed;
HTML uses content-versioned URLs and no-cache headers. No source map is served.
The Vazirmatn font is local and distributed under `webadmin/fonts/OFL.txt`.

The original JSX cannot be recovered exactly. Further component-level redesign
should extract individual screens into new source modules, not patch the
production minified file. No UI framework rewrite is needed for deployment.

New application components can now be imported normally: `components/password-change.js`
is extracted from the recovered entry and receives its UI/API dependencies explicitly.
The build bundles these modules; legacy screens/vendor code are still in the recovered entry.
This is incremental modularization, not a claim that all original components were reconstructed.
