"""Initialize per-user BeatKeys data and migrate the legacy configuration."""

import json
from pathlib import Path
import shutil


DATA_DIR = Path.home() / ".beatkeys"

DATA_README = """# BeatKeys / 动次打次：用户数据目录

本目录保存当前用户的软件设置和录音，不是程序安装目录。

## 文件用途

- config.json：命令、快捷键、命令顺序、音频设备、识别参数和录音保存目录。
- samples/：默认录音目录，按命令 ID 分组保存 WAV 原始录音。
- README.md：本说明。

录音目录可以在软件设置中更改。更改后，录音可能位于本目录之外；
备份或清理时请同时检查设置中的实际路径。
关闭软件后再手动修改、搬移或恢复这些文件，避免与软件保存操作冲突。
删除配置会丢失命令及设置；删除录音会丢失对应的匹配样本。

## 录音与生物特征隐私

声音可能暴露声纹等可用于辨识个人的生物特征，也可能包含对话、姓名、
环境声音及其他人的隐私。请把原始录音视为敏感数据。

- 不要将本目录或录音目录提交到 GitHub 等代码仓库、公开网盘或群聊。
- 分享错误日志或截图前，检查是否包含个人路径、命令名称等私人信息。
  排查问题时不要直接打包整个数据目录发送给他人。
- 避免把录音目录放在公开共享或自动上传的同步目录中；使用同步或备份时，
  先确认访问权限、共享范围和保存期限。
- 共享电脑时使用独立系统账户；需要更强保护时使用系统磁盘加密或加密备份。
- 录制他人的声音前先征得同意，不再需要的录音请及时清理，并检查备份副本。

本软件在本地处理录音，不主动上传录音；但配置和 WAV 文件以未加密形式保存。
本地处理并不防止其他程序、同机用户、恶意软件或同步服务读取这些文件，
也不保证删除后的数据无法恢复。
"""


def initialize_user_data():
    """Keep existing user settings; create defaults only for a new installation."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    readme = DATA_DIR / "README.md"
    if not readme.exists():
        readme.write_text(DATA_README, encoding="utf-8")
    config_path = DATA_DIR / "config.json"
    if not config_path.exists():
        legacy = Path(__file__).with_name("config.json")
        if legacy.exists():
            shutil.move(str(legacy), str(config_path))
        else:
            defaults = {
                "device_index": None,
                "match_threshold": 1.1,
                "speech_start_rms": 0.01,
                "speech_end_rms": 0.006,
                "end_silence_seconds": 0.45,
                "min_utterance_seconds": 0.35,
                "max_utterance_seconds": 3.0,
                "cooldown_seconds": 0.8,
                "toggle_hotkey": "",
                "samples_directory": str(DATA_DIR / "samples"),
                # First-run presets only; existing user commands are never replaced.
                "commands": [
                    {"id": "redo", "name": "重做一步", "hotkey": "shift+ctrl+z"},
                    {"id": "undo", "name": "撤销一步（注意检查是不是ctrl+z）",
                     "hotkey": "ctrl+alt+z"},
                    {"id": "free-transform", "name": "自由变换", "hotkey": "ctrl+t"},
                    {"id": "new-layer", "name": "新建图层", "hotkey": "ctrl+shift+n"},
                    {"id": "save", "name": "保存文件", "hotkey": "ctrl+s"},
                    {"id": "deselect", "name": "取消选区", "hotkey": "ctrl+d"},
                    {"id": "fill-selection", "name": "填充选区", "hotkey": "alt+delete"},
                    {"id": "lasso", "name": "套索", "hotkey": "l"},
                    {"id": "eyedropper", "name": "吸管", "hotkey": "i"},
                    {"id": "brush", "name": "画笔", "hotkey": "b"},
                ],
            }
            config_path.write_text(
                json.dumps(defaults, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return config_path
