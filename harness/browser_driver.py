"""Real Chrome driver: fixed operations, pre-request document scope enforcement."""

import base64
import os
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from harness.browser_artifacts import descriptor
from harness.browser_cdp import CDPConnection, pipe_command
from harness.browser_models import in_scope
from harness.browser_scripts import ASSERT, INTERACT, MASK, TARGET_GUARD, UNMASK
from harness.execution_logs import redact


class BrowserUncertain(ValueError):
    pass


def chrome_executable():
    configured = os.environ.get("HARNESS_BROWSER_EXECUTABLE")
    if configured is not None:
        path = Path(configured)
        if (
            not path.is_absolute()
            or not path.is_file()
            or not os.access(path, os.X_OK)
            or any(ord(c) < 32 for c in configured)
        ):
            raise ValueError("HARNESS_BROWSER_EXECUTABLE must name an absolute executable file")
        return str(path.resolve())
    for candidate in [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]:
        if candidate and Path(candidate).is_file():
            return candidate
    raise OSError("Chrome/Chromium unavailable")


class BrowserDriver:
    def __init__(self, plan, runtime):
        self.plan, self.runtime = plan, Path(runtime)
        self.deadline = time.monotonic() + plan["timeouts"]["run_ms"] / 1000
        self.violation = None
        self.process = None
        self.profile = None
        self.fds = []
        self.session = None
        self.target = None
        self.supported_methods = []
        try:
            if self.runtime.is_symlink():
                raise ValueError("private runtime symlink refused")
            self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.runtime, 0o700)
            self.profile = tempfile.mkdtemp(prefix="profile-", dir=self.runtime)
            child_read, parent_write = os.pipe()
            parent_read, child_write = os.pipe()
            self.fds = [parent_read, parent_write, child_read, child_write]

            self.process = subprocess.Popen(
                pipe_command(
                    child_read,
                    child_write,
                    [
                        chrome_executable(),
                        "--headless=new",
                        "--remote-debugging-pipe",
                        "--user-data-dir=" + self.profile,
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-extensions",
                        "--disable-background-networking",
                        "--disable-sync",
                        "--disable-component-update",
                        "--disable-default-apps",
                        "about:blank",
                    ],
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(child_read, child_write),
                start_new_session=True,
            )
            os.close(child_read)
            os.close(child_write)
            self.fds = [parent_read, parent_write]
            self.cdp = CDPConnection(parent_read, parent_write)
            self.cdp.handler = self._event
            self._call("Browser.getVersion", browser=True)
            self._call("Browser.setDownloadBehavior", {"behavior": "deny"}, browser=True)
            # Existing blank target is attached under browser auto-attach before any navigation.
            self._call(
                "Target.setAutoAttach",
                {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True},
                browser=True,
            )
            while self.session is None:
                self.cdp.pump(self._deadline())
            self._check()
        except BaseException:
            self.close()
            raise

    def _deadline(self):
        return min(
            self.deadline,
            getattr(self, "step_deadline", float("inf")),
            time.monotonic() + self.plan["timeouts"]["action_ms"] / 1000,
        )

    def _call(self, method, params=None, *, browser=False, session=None):
        result = self.cdp.call(
            method,
            params,
            deadline=self._deadline(),
            session=None if browser else (session or self.session),
        )
        self.supported_methods.append(method)
        return result

    def _check(self):
        if self.violation:
            raise ValueError(self.violation)
        if time.monotonic() >= self.deadline:
            raise TimeoutError("browser total deadline exceeded")

    def _event(self, event):
        method, params = event.get("method"), event.get("params", {})
        sid = event.get("sessionId")
        if method == "Target.attachedToTarget":
            session, info = params["sessionId"], params["targetInfo"]
            if (
                self.session is not None
                or info["type"] != "page"
                or info.get("url") not in {"", "about:blank"}
            ):
                self.violation = (
                    "Unsupported additional browser target blocked "
                    f"(type={info.get('type')!r}, existing_session={self.session is not None}, "
                    f"url={str(info.get('url', ''))[:120]!r})"
                )
                self._call("Target.closeTarget", {"targetId": info["targetId"]}, browser=True)
                return
            self.session, self.target = session, info["targetId"]
            self._call("Page.enable")
            self._call("Runtime.addBinding", {"name": "__harnessUnsupportedTarget"})
            self._call("Page.addScriptToEvaluateOnNewDocument", {"source": TARGET_GUARD})
            self._call("Network.enable")
            self._call("Network.setBypassServiceWorker", {"bypass": True})
            self._call("Network.setCacheDisabled", {"cacheDisabled": True})
            self._call(
                "Fetch.enable",
                {
                    "patterns": [
                        {"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}
                    ],
                    "handleAuthRequests": True,
                },
            )
            self._call(
                "Target.setAutoAttach",
                {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True},
            )
            self._call(
                "Emulation.setDeviceMetricsOverride",
                self.plan["viewport"] | {"deviceScaleFactor": 1, "mobile": False},
            )
            self._call("Runtime.runIfWaitingForDebugger")
        elif (
            method == "Runtime.bindingCalled" and params.get("name") == "__harnessUnsupportedTarget"
        ):
            self.violation = "Unsupported popup or worker blocked before creation"
        elif method == "Fetch.requestPaused":
            allowed = False
            try:
                allowed = in_scope(params["request"]["url"], self.plan["navigation_scope"])
            except ValueError:
                pass
            if allowed and not self.violation:
                self._call("Fetch.continueRequest", {"requestId": params["requestId"]}, session=sid)
            else:
                self.violation = "Out-of-scope document navigation blocked before request"
                self._call(
                    "Fetch.failRequest",
                    {"requestId": params["requestId"], "errorReason": "BlockedByClient"},
                    session=sid,
                )
        elif method == "Fetch.authRequired":
            self.violation = "Browser authentication unsupported"
            self._call(
                "Fetch.continueWithAuth",
                {
                    "requestId": params["requestId"],
                    "authChallengeResponse": {"response": "CancelAuth"},
                },
                session=sid,
            )
        elif method == "Page.javascriptDialogOpening":
            self.violation = "Browser dialog refused"
            self._call("Page.handleJavaScriptDialog", {"accept": False}, session=sid)
        elif method == "Page.frameNavigated":
            url = params.get("frame", {}).get("url", "about:blank")
            if url != "about:blank":
                try:
                    if not in_scope(url, self.plan["navigation_scope"]):
                        self.violation = "Unexpected document destination"
                except ValueError:
                    self.violation = "Unsupported document destination"

    def _function(self, body, args):
        remote = self._call(
            "Runtime.evaluate", {"expression": "globalThis", "returnByValue": False}
        )
        object_id = remote.get("result", {}).get("objectId")
        if not object_id:
            raise ValueError("browser document unavailable")
        value = self._call(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": body,
                "arguments": [{"value": a} for a in args],
                "returnByValue": True,
            },
        )
        if value.get("exceptionDetails") or value.get("result", {}).get("type") != "object":
            raise ValueError("browser operation exception")
        result = value["result"].get("value")
        if not isinstance(result, dict):
            raise ValueError("malformed browser operation result")
        return result

    def _step(self, step):
        op = step["op"]
        self._check()
        if op == "navigate":
            if not in_scope(step["url"], self.plan["navigation_scope"]):
                raise ValueError("navigation outside approved scope")
            response = self._call("Page.navigate", {"url": step["url"]})
            if response.get("errorText"):
                raise ValueError("document navigation failed")
            return {"ok": True}
        if op == "assert_url":
            return self._function(
                "function(expected){return {ok:location.href===expected};}", [step["equals"]]
            )
        if op in {"click", "fill"}:
            return self._function(INTERACT, [step["selector"], op, step.get("value")])
        return self._function(ASSERT, [step["selector"], op, step.get("equals")])

    def run_criterion(self, criterion):
        observations, status, error = [], "passed", None
        try:
            for step in criterion["steps"]:
                deadline = self._deadline()
                self.step_deadline = deadline
                while True:
                    try:
                        result = self._step(step)
                    except (OSError, TimeoutError) as exc:
                        if not step["op"].startswith("assert_"):
                            raise BrowserUncertain(
                                "Browser interaction acknowledgement lost"
                            ) from exc
                        raise
                    self._check()
                    if result.get("ok") is True:
                        observations.append({"op": step["op"], "ok": True})
                        break
                    if not step["op"].startswith("assert_") or time.monotonic() >= deadline:
                        raise ValueError(result.get("error", "Browser assertion mismatch"))
                    # The CDP call pumps events on each read-only assertion; no interaction retry.
                    time.sleep(0.03)
        except BrowserUncertain as exc:
            status, error = "uncertain", str(exc)
        except (ValueError, OSError, TimeoutError) as exc:
            status, error = "failed", redact(str(exc))
        self.step_deadline = float("inf")
        evidence = []
        if self.plan["evidence"]["screenshot"] == "each_criterion" and status != "uncertain":
            masked = None
            try:
                selectors = ["input[type=password]"] + self.plan["evidence"]["mask_selectors"]
                masked = self._function(MASK, [selectors])
                if masked.get("ok") is not True:
                    raise ValueError("required screenshot mask unavailable")
                response = self._call("Page.captureScreenshot", {"format": "png"})
                name = "image-" + uuid.uuid4().hex + ".png"
                evidence.append(
                    descriptor(
                        self.runtime,
                        name,
                        base64.b64decode(response["data"], validate=True),
                        "image/png",
                    )
                )
            except (ValueError, OSError, TimeoutError, KeyError):
                status, error = "blocked", "Required screenshot evidence unavailable"
            finally:
                if masked and masked.get("ok") is True:
                    try:
                        if self._function(UNMASK, [masked["masks"]]).get("ok") is not True:
                            raise ValueError("mask restoration failed")
                    except (ValueError, OSError, TimeoutError):
                        status, error = "blocked", "Screenshot mask restoration unavailable"
        return {
            "status": status,
            "observations": observations,
            "error": error,
            "evidence": evidence,
        }

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=3)
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds = []
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)
