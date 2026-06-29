"""
服务器配置
"""
import time
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 输出根目录
OUTPUT_DIR = PROJECT_ROOT / "output"
TASKS_DIR = OUTPUT_DIR / "tasks"
TASK_INDEX_FILE = OUTPUT_DIR / "task_index.json"

# 服务器
HOST = "0.0.0.0"
PORT = 8000

# 发布控制
BUILD_ID = str(int(time.time()))  # 每次启动生成新 build ID，确保前端刷新
PROTOCOL_VERSION = "1.0"          # 前后端协议版本，不匹配时前端自动刷新

# 并发限制
MAX_CONCURRENT_TASKS = 2  # 同时运行的最大分析任务数

# 上传限制（字节）
MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB

# 任务自动清理（秒），0 表示不自动清理
TASK_RETENTION_SECONDS = 7 * 24 * 3600  # 7 天

# ── 任务状态机 ──
TASK_STATUS = {
    "PENDING": "pending",
    "UPLOADED": "uploaded",
    "VISUAL_RUNNING": "visual_running",
    "SPEECH_RUNNING": "speech_running",
    "GESTURE_RUNNING": "gesture_running",
    "SCORING": "scoring",
    "REPORTING": "reporting",
    "COMPLETED": "completed",
    "FAILED": "failed",
}

# 状态流转顺序
STATUS_ORDER = [
    "pending",
    "uploaded",
    "visual_running",
    "speech_running",
    "gesture_running",
    "scoring",
    "reporting",
    "completed",
]
