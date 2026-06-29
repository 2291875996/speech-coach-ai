"""
JSON 文件任务存储
线程安全的简单文件索引，无需数据库依赖。
"""
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from server.config import TASK_INDEX_FILE

_lock = threading.Lock()


def _read_index() -> List[Dict]:
    """读取任务索引（线程安全）。"""
    if not TASK_INDEX_FILE.exists():
        return []
    try:
        with _lock:
            with open(TASK_INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _write_index(tasks: List[Dict]) -> None:
    """写入任务索引（线程安全）。"""
    TASK_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        tmp = str(TASK_INDEX_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(tasks, f, indent=2, ensure_ascii=False)
        os.replace(tmp, TASK_INDEX_FILE)


def create_entry(task_id: str, filename: str, task_dir: str) -> Dict:
    """创建新任务条目并写入索引。"""
    entry = {
        "id": task_id,
        "filename": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "overall_score": None,
        "grade": None,
        "dimension_scores": None,
        "task_dir": task_dir,
        "error_message": None,
    }
    tasks = _read_index()
    tasks.insert(0, entry)
    _write_index(tasks)
    return entry


def update_entry(task_id: str, **kwargs) -> Optional[Dict]:
    """更新任务条目。"""
    tasks = _read_index()
    for i, t in enumerate(tasks):
        if t["id"] == task_id:
            tasks[i].update(kwargs)
            _write_index(tasks)
            return tasks[i]
    return None


def get_entry(task_id: str) -> Optional[Dict]:
    """获取单个任务条目。"""
    for t in _read_index():
        if t["id"] == task_id:
            return t
    return None


def list_entries(limit: int = 50, offset: int = 0) -> List[Dict]:
    """列出任务，按时间倒序。"""
    tasks = _read_index()
    return tasks[offset:offset + limit]


def delete_entry(task_id: str) -> bool:
    """从索引中删除任务条目。"""
    tasks = _read_index()
    new_tasks = [t for t in tasks if t["id"] != task_id]
    if len(new_tasks) != len(tasks):
        _write_index(new_tasks)
        return True
    return False
