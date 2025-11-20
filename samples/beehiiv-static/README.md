# beehiiv demo shell

This directory recreates the provided beehiiv HTML shell with locally served placeholder assets, so you can run it with a simple static server.

## Run locally

From the repository root:

```bash
cd samples/beehiiv-static
python -m http.server 3000
```

Then open <http://localhost:3000> in your browser. All referenced assets are stubbed to avoid 404 errors; replace the contents as needed with real bundles or embeds.

## Notes

- The page keeps the third-party script tags (Mapbox, Google, ProfitWell, Ada, Cloudflare) intact.
- Stub files in this folder mirror the filenames referenced by the HTML so you can drop in real builds without changing the markup.
- The root content includes a simple message to confirm the page loaded correctly; adjust as desired.
