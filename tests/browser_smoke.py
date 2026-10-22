"""Run explicitly after installing Playwright; not required by the core suite."""

import json
import os
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
            browser = playwright.chromium.launch(**({"executable_path": os.environ["SCENEDIFF_CHROMIUM"]} if os.environ.get("SCENEDIFF_CHROMIUM") else {}))
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

            # Applying one selected property preserves other target edits.
            source_a='[gd_scene format=3]\n[node name="World" type="Node3D"]\nposition = Vector3(1, 2, 3)\nspeed = 5\n'
            source_b=source_a.replace('Vector3(1, 2, 3)','Vector3(0, 0, 0)').replace('speed = 5','speed = 9')
            page.locator("#new-comparison").click()
            page.locator("#file-base").set_input_files([])
            for field,text in [("file-a",source_a),("file-b",source_b)]:
                page.locator("#"+field).set_input_files({"name":"world.tscn","mimeType":"text/plain","buffer":text.encode()})
            page.locator("#submit-compare").click()
            expect(page.locator("#compare-dialog")).not_to_be_visible()
            page.locator('[data-entity="node:."]').click()
            page.locator('[data-property="position"]').click()
            page.locator('[data-apply="1"][data-scope="property"]').click()
            expect(page.locator("#download-b")).to_be_enabled()
            expect(page.locator("#source-badge")).to_have_text("MERGE PREVIEW")
            with page.expect_download() as merged_event:
                page.locator("#download-b").click()
            merged_path=Path(directory)/'merged.tscn'
            merged_event.value.save_as(merged_path)
            assert 'position = Vector3(1, 2, 3)' in merged_path.read_text()
            assert 'speed = 9' in merged_path.read_text()
            assert merged_event.value.suggested_filename=='world.merged-B.tscn'
            page.screenshot(path="/tmp/scenediff-merge.png",full_page=True)
            with page.expect_download() as saved_event:
                page.locator("#save-report").click()
            merged_report=Path(directory)/'merged.html'
            saved_event.value.save_as(merged_report)
            offline.goto(merged_report.as_uri())
            expect(offline.locator("#download-b")).to_be_enabled()
            with offline.expect_download() as offline_event:
                offline.locator("#download-b").click()
            offline_path=Path(directory)/'offline.tscn'
            offline_event.value.save_as(offline_path)
            assert offline_path.read_bytes()==merged_path.read_bytes()
            page.locator("#undo-merge").click()
            expect(page.locator("#download-b")).to_be_disabled()
            page.locator('[data-property="speed"]').click()
            page.locator('[data-apply="2"][data-scope="property"]').click()
            expect(page.locator("#download-a")).to_be_enabled()
            with page.expect_download() as reverse_event:
                page.locator("#download-a").click()
            reverse_path=Path(directory)/'reverse.tscn'
            reverse_event.value.save_as(reverse_path)
            assert 'speed = 9' in reverse_path.read_text()
            assert 'position = Vector3(1, 2, 3)' in reverse_path.read_text()

            # Whole-node copying adds children; missing parents produce a visible error.
            subtree='[node name="Enemy" type="Node" parent="."]\n[node name="Body" type="Node" parent="Enemy"]\n'
            page.locator("#new-comparison").click()
            page.locator("#file-a").set_input_files({"name":"world.tscn","mimeType":"text/plain","buffer":(source_a+subtree).encode()})
            page.locator("#file-b").set_input_files({"name":"world.tscn","mimeType":"text/plain","buffer":source_b.encode()})
            page.locator("#submit-compare").click()
            expect(page.locator("#compare-dialog")).not_to_be_visible()
            expect(page.locator("#download-a")).to_be_disabled()
            page.locator('[data-entity="node:Enemy/Body"]').click()
            page.locator('[data-apply="1"][data-scope="entity"]').click()
            expect(page.locator("#merge-error")).to_contain_text("parent")
            expect(page.locator("#download-b")).to_be_disabled()
            page.locator('[data-entity="node:Enemy"]').click()
            page.locator('[data-apply="1"][data-scope="entity"]').click()
            expect(page.locator("#download-b")).to_be_enabled()
            with page.expect_download() as node_event:
                page.locator("#download-b").click()
            node_path=Path(directory)/'node.tscn'
            node_event.value.save_as(node_path)
            assert 'name="Enemy"' in node_path.read_text()
            assert 'name="Body"' in node_path.read_text()
            assert 'speed = 9' in node_path.read_text()

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
            print("Browser checks passed: comparison, decisions, exports, offline report, file input, selective merges, merged downloads, undo, escaping, responsive layout.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()

