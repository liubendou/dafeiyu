"""Where to look. Paths are config, never hard-coded business logic."""

import json
import os

# <desktop>/core/dfy_core/config.py -> up 3 = <desktop>
DESKTOP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT_DIR = os.path.dirname(DESKTOP_DIR)  # .../大肥鱼
HOME = os.path.expanduser("~")

RUNTIME_DIR = os.path.join(DESKTOP_DIR, ".runtime")
PLUGIN_NAME = "astrbot_plugin_dafeiyu_social"

DEFAULTS = {
    "astrbot_dir": os.path.join(HOME, "astrbot"),
    "plugin_dir": os.path.join(HOME, "astrbot", "data", "plugins", PLUGIN_NAME),
    "plugin_data_dir": os.path.join(HOME, "astrbot", "data", "plugin_data", PLUGIN_NAME),
    "cmd_config": os.path.join(HOME, "astrbot", "data", "cmd_config.json"),
    "plugin_config": os.path.join(HOME, "astrbot", "data", "config", PLUGIN_NAME + "_config.json"),
    "persona_file": os.path.join(HOME, "astrbot", "personas", "dafeiyu_system_prompt.md"),
    "ops_dfy_dir": os.path.join(HOME, "ops-dfy"),
    "ports": {
        "astrbot_webui": 6185,
        "astrbot_onebot": 6199,
        "napcat_webui": 6099,
        "napcat_http": 3000,
        "napcat_ws": 3001,
        "legacy_bridge": 3100,
        "legacy_arena": 8790,
        "legacy_dsh": 43129
    },
    "expected_whitelist": {
        "owner": "<OWNER_QQ>",
        "groups": ["<GROUP_A>", "<GROUP_B>"],
        "forbidden_group": "<FORBIDDEN_GROUP>"
    },
    # Arena 网关：能力从 ~/ops-dfy/ctl/agent_ctl.py 搬进核心，8790 不再使用。
    # 上游地址只在这一个模块里窄口读取，读到即加入脱敏名单，任何路由都不回显。
    "arena_endpoint_file": os.path.join(HOME, "ops-dfy", "ctl", "mcp_url.txt"),
    "arena_notes_file": os.path.join(HOME, "ops-dfy", "ctl", "model_notes.json"),
    "arena_requests_dir": os.path.join(DESKTOP_DIR, ".runtime", "arena", "requests"),
    "arena_workbench_root": os.path.join(HOME, "AppData", "Local", "LiubendouWorkbench"),
    "arena_profile_scope": "profile_3",
    "read_limit_bytes": 4194304,
    # 发送：写入 AstrBot 插件的 outbox，由插件经 AstrBot 现有连接发出。
    # 关掉它，桌面端只能看不能发（设置页同一个开关）。
    "send_enabled": True,
    "outbox_max_chars": 1200
}


def load():
    cfg = dict(DEFAULTS)
    p = os.path.join(DESKTOP_DIR, "config", "desktop.config.json")
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                user = json.load(fh)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    merged = dict(cfg[k])
                    merged.update(v)
                    cfg[k] = merged
                else:
                    cfg[k] = v
        except Exception as e:
            # A malformed local config must never take the status page down.
            cfg["_config_error"] = "%s: %s" % (type(e).__name__, e)
    return cfg


def state_file(cfg):
    """Where the plugin keeps its own state.json.

    DFY_STATE_TARGET exists only so the self-test can run against a scratch copy;
    production never sets it.
    """
    return os.environ.get("DFY_STATE_TARGET") or os.path.join(cfg["plugin_data_dir"], "state.json")


def runtime_dir():
    """Where the app keeps its own files (audit, baselines, backups).

    DFY_RUNTIME_DIR only exists so the self-tests can run against a scratch
    directory instead of the live one.
    """
    d = os.environ.get("DFY_RUNTIME_DIR") or RUNTIME_DIR
    os.makedirs(d, exist_ok=True)
    return d
