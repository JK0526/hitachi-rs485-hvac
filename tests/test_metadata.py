import json
import tomllib
from pathlib import Path

from custom_components.hitachi_rs485.const import VERSION

ROOT = Path(__file__).parents[1]


def test_versions_manifest_and_translations():
    base = ROOT / "custom_components" / "hitachi_rs485"
    manifest = json.loads((base / "manifest.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert manifest["version"] == project["project"]["version"] == VERSION == "0.1.1rc2"
    assert manifest["requirements"] == []
    assert (
        manifest["integration_type"] == "hub"
        and manifest["iot_class"] == "local_polling"
    )
    assert json.loads((ROOT / "hacs.json").read_text())["homeassistant"] == "2026.10.0"
    strings = json.loads((base / "strings.json").read_text())
    en = json.loads((base / "translations" / "en.json").read_text())
    zh = json.loads((base / "translations" / "zh-Hant.json").read_text())
    assert strings == en

    def leaves(d, prefix=""):
        if isinstance(d, dict):
            return {
                k
                for name, value in d.items()
                for k in leaves(value, f"{prefix}/{name}")
            }
        return {prefix}

    assert leaves(en) == leaves(zh)
    assert zh["entity"]["sensor"]["energy"]["name"] == "累積用電量"
    assert "確認並儲存" == zh["options"]["step"]["summary"]["menu_options"]["confirm"]


async def test_back_never_commits(hass, entry, device):
    from custom_components.hitachi_rs485.config_flow import (
        HitachiConfigFlow,
        HitachiOptionsFlow,
    )
    from custom_components.hitachi_rs485.scanner import ScanResult

    flow = HitachiConfigFlow()
    flow.hass = hass
    flow.handler = "hitachi_rs485"
    flow.flow_id = "back"
    flow._found = {1: ScanResult(device, True)}
    assert (await flow.async_step_back())["step_id"] == "select"
    options = HitachiOptionsFlow()
    options.hass = hass
    options.handler = entry.entry_id
    options.flow_id = "back-options"
    await options.async_step_init()
    options._draft["devices"] = []
    assert (await options.async_step_back())["step_id"] == "menu"
    assert len(entry.options["devices"]) == 1
