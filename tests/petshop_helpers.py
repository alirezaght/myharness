"""Small helpers for serving and inspecting the real static petshop in Chrome."""

import hashlib
import os
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from harness.browser_driver import BrowserDriver


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class LocalPetshop:
    def __init__(self, source: Path):
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            lambda *args, **kwargs: QuietStaticHandler(*args, directory=str(source), **kwargs),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}/"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("petshop HTTP server thread did not stop")


def browser_plan(origin: str, width: int, height: int) -> dict:
    return {
        "timeouts": {"action_ms": 3000, "run_ms": 45000},
        "viewport": {"width": width, "height": height},
        "navigation_scope": [{"origin": origin, "path_prefix": "/"}],
        "evidence": {"screenshot": "each_criterion", "mask_selectors": []},
    }


def criterion(*steps: dict) -> dict:
    return {"steps": list(steps)}


def run(driver: BrowserDriver, *steps: dict) -> dict:
    result = driver.run_criterion(criterion(*steps))
    assert result["status"] == "passed", result
    return result


def navigate(url: str) -> dict:
    return {"op": "navigate", "url": url}


def click(selector: str) -> dict:
    return {"op": "click", "selector": selector}


def assert_text(selector: str, equals: str) -> dict:
    return {"op": "assert_text", "selector": selector, "equals": equals}


def record_screenshot(driver: BrowserDriver, result: dict, name: str, directory: Path) -> dict:
    """Give a BrowserDriver screenshot a stable name and retain its SHA-256 sidecar."""
    assert result["status"] == "passed", result
    evidence = next(item for item in result["evidence"] if item["media_type"] == "image/png")
    source = driver.runtime / evidence["path"]
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    shutil.copyfile(source, destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    (directory / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")
    return {"path": str(destination), "sha256": digest}


def evaluate(driver: BrowserDriver, expression: str) -> dict:
    """Run a read-only expression through the driver's existing CDP session."""
    result = driver._call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
    )
    remote = result.get("result", {})
    if remote.get("type") != "object" or "value" not in remote:
        raise AssertionError(f"browser expression did not return an object: {remote}")
    return remote["value"]


def inspect_controls(driver: BrowserDriver) -> dict:
    return evaluate(
        driver,
        """(() => {
          const controls = [...document.querySelectorAll('button')].map((button) => {
            button.scrollIntoView({block: 'center', inline: 'nearest'});
            const rect = button.getBoundingClientRect();
            const style = getComputedStyle(button);
            const target = document.elementFromPoint(
              rect.left + rect.width / 2,
              rect.top + rect.height / 2
            );
            return {
              name: button.getAttribute('aria-label') || button.textContent,
              left: rect.left,
              right: rect.right,
              top: rect.top,
              bottom: rect.bottom,
              width: rect.width,
              height: rect.height,
              visible: style.display !== 'none' && style.visibility !== 'hidden',
              hitTarget: target === button || button.contains(target)
            };
          });
          return {
            width: innerWidth,
            height: innerHeight,
            documentWidth: document.documentElement.scrollWidth,
            documentHeight: document.documentElement.scrollHeight,
            controls
          };
        })()""",
    )


def scroll_cart_into_view(driver: BrowserDriver) -> None:
    evaluate(
        driver,
        "(() => { const cart = document.querySelector('.cart'); "
        "cart.scrollIntoView({block: 'center', inline: 'nearest'}); "
        "return {top: cart.getBoundingClientRect().top, "
        "height: cart.getBoundingClientRect().height}; })()",
    )


def dispatch_tab(driver: BrowserDriver) -> None:
    driver._call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Tab", "code": "Tab"})
    driver._call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Tab", "code": "Tab"})


def artifact_directory(tmp_path: Path) -> Path:
    return Path(os.environ.get("HARNESS_PETSHOP_SCREENSHOT_DIR", tmp_path / "petshop-shots"))
