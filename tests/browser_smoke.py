"""Run explicitly after installing Playwright; not required by the core suite."""

import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright, expect
from scenediff.server import create_server


def main():
    server = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright, tempfile.TemporaryDirectory() as directory:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1550, "height": 1050}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            expect(page.locator(".brand strong")).to_have_text("SceneDiff")
            expect(page.locator("#source-badge")).to_have_text("EXAMPLE PROJECT")
            page.locator('[data-entity="node:Sun"]').click()
            expect(page.locator("#entity-title")).to_have_text("World/Sun")
            page.locator('[data-property="light_energy"]').click()
            page.locator('[data-choice="right"]').click()
            expect(page.locator("#resolution")).to_contain_text("Decision recorded")
            page.screenshot(path="/tmp/scenediff-ui.png", full_page=True)

            with page.expect_download() as download_event:
                page.locator("#export-plan").click()
            plan_path = Path(directory) / "plan.json"
            download_event.value.save_as(plan_path)
            plan = json.loads(plan_path.read_text())
            assert any(d["property"] == "light_energy" and d["choice"] == "right" for d in plan["decisions"])
            assert plan["unresolved"]

            with page.expect_download() as download_event:
                page.locator("#save-report").click()
            report_path = Path(directory) / "report.html"
            download_event.value.save_as(report_path)
            offline = browser.new_page()
            offline.on("pageerror", lambda error: errors.append(str(error)))
            offline.goto(report_path.as_uri())
            expect(offline.locator("#new-comparison")).to_have_text("Open local app")
            offline.locator('[data-entity="node:Sun"]').click()
            offline.locator('[data-property="light_energy"]').click()
            expect(offline.locator("#resolution")).to_contain_text("Decision recorded")

            page.locator('[data-entity="node:Enemies/Goblin"]').click()
            expect(page.locator("#entity-subtitle")).to_contain_text("Added")
            page.get_by_role("tab", name="Raw diff").click()
            expect(page.locator(".code-diff")).to_contain_text('name="Goblin"')
            page.locator("#new-comparison").click()
            page.locator('[data-mode="files"]').click()
            for field, version in [("file-base","base"),("file-a","alice"),("file-b","bob")]:
                page.locator("#" + field).set_input_files(Path(__file__).resolve().parents[1] / "scenediff" / "examples" / version / "ground.tres")
            page.locator("#submit-compare").click()
            expect(page.locator("#compare-dialog")).not_to_be_visible()
            expect(page.locator("#breadcrumb")).to_have_text("ground.tres")
            expect(page.locator("#source-badge")).to_have_text("LOCAL FILES")

            # Untrusted scene strings must remain text in both live and saved views.
            hostile = '[gd_scene format=3]\n[node name="<img src=x onerror=alert(1)>" type="Node"]\n'
            page.locator("#new-comparison").click()
            page.locator("#file-base").set_input_files([])
            for field in ["file-a", "file-b"]:
                page.locator("#" + field).set_input_files({"name": "hostile.tscn", "mimeType": "text/plain", "buffer": hostile.encode()})
            page.locator("#submit-compare").click()
            expect(page.locator("#compare-dialog")).not_to_be_visible()
            expect(page.locator("#entity-title")).to_have_text("<img src=x onerror=alert(1)>")
            assert page.locator("img").count() == 0
            page.set_viewport_size({"width": 700, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
            browser.close()
            print("Browser checks passed: comparison, decisions, exports, offline report, file input, escaping, responsive layout.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
