# Good Paws Pet Shop

This is a small static browser example with three products and a page-local basket. It uses no
backend, account, checkout, payment service, or persistent storage. Prices are integer cents in
`app.js`; the basket starts empty whenever the page loads.

## Run the real browser demo

From the repository root, run:

```sh
uv run pytest -m browser tests/test_petshop_browser.py
```

The test serves these exact files from `examples/petshop` on a temporary `127.0.0.1` HTTP port,
starts the configured Chrome executable through the repository's `BrowserDriver`, and closes both
the browser and server when the test finishes. Set `HARNESS_BROWSER_EXECUTABLE` to an absolute,
executable Chrome/Chromium path if it is not on the standard path. The environment used for the
Phase 5 run also sets `HARNESS_PETSHOP_SCREENSHOT_DIR` (for example, `/artifacts/petshop`) to retain
stable desktop and mobile screenshots with `.sha256` sidecars.

The repeatable scenarios cover the catalog, Dog Toy and Cat Bowl quantities, the €28.00 and €20.00
totals, Bird Seed, removing the final unit, clearing, reload reset, keyboard focus, and desktop
1280×800 and mobile 375×812 layouts. Screenshots and browser output are local evidence; loopback is
classified as `local_browser_fixture`. The HTTP server delivers the actual repository source files,
but this does not establish a remote deployment, provider permission, or a separately approved
Harness browser plan.

To browse manually, serve this directory with any local static HTTP server, such as:

```sh
python3 -m http.server 8000 --directory examples/petshop
```

Open `http://127.0.0.1:8000/`. Stop the server with Ctrl+C when finished.
