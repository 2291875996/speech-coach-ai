"""
任务管理器
负责任务的创建、查询、状态更新、目录管理和后台分析调度。
"""
import concurrent.futures
import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from server.config import TASKS_DIR, MAX_CONCURRENT_TASKS, TASK_STATUS
from server import db

logger = logging.getLogger("interview_analyzer.task_manager")

# 模块级单例
_manager: Optional["TaskManager"] = None


def get_manager() -> "TaskManager":
    """获取 TaskManager 单例。"""
    global _manager
    if _manager is None:
        _manager = TaskManager()
    return _manager


class TaskManager:
    """任务生命周期管理。"""

    def __init__(self):
        self._tasks_dir = TASKS_DIR
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_TASKS,
            thread_name_prefix="pipeline-",
        )
        self._lock = threading.Lock()

    def create_task(self, filename: str) -> Dict:
        """创建新任务：生成 UUID、建立目录结构、写入索引。"""
        task_id = str(uuid.uuid4())
        task_dir = self._tasks_dir / task_id

        # 创建子目录
        (task_dir / "uploads").mkdir(parents=True, exist_ok=True)
        (task_dir / "features").mkdir(parents=True, exist_ok=True)
        (task_dir / "reports").mkdir(parents=True, exist_ok=True)
        (task_dir / "logs").mkdir(parents=True, exist_ok=True)

        # 写入 meta.json
        meta = {
            "id": task_id,
            "filename": filename,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": TASK_STATUS["PENDING"],
            "steps": {},
            "result": None,
            "error": None,
            "timing": {},
        }
        with open(task_dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        # 写入全局索引
        entry = db.create_entry(task_id, filename, str(task_dir))
        return entry

    def get_task(self, task_id: str) -> Optional[Dict]:
        """获取任务完整信息。"""
        entry = db.get_entry(task_id)
        if entry is None:
            return None

        # 合并 meta.json 中的详细信息
        meta_path = Path(entry["task_dir"]) / "meta.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                entry = {**entry, **meta}
            except (json.JSONDecodeError, OSError):
                pass
        return entry

    def update_status(self, task_id: str, status: str, **extra) -> Optional[Dict]:
        """更新任务状态（同时更新索引和 meta.json）。"""
        # 更新索引
        entry = db.update_entry(task_id, status=status, **extra)
        if entry is None:
            return None

        # 更新 meta.json
        meta_path = Path(entry["task_dir"]) / "meta.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except (json.JSONDecodeError, OSError):
                meta = {}
            meta["status"] = status
            meta.update(extra)
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)

        return entry

    def update_step(self, task_id: str, step_name: str, step_data: Dict) -> None:
        """更新 meta.json 中某一步的状态。"""
        entry = db.get_entry(task_id)
        if entry is None:
            return
        meta_path = Path(entry["task_dir"]) / "meta.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except (json.JSONDecodeError, OSError):
                meta = {}
            meta.setdefault("steps", {})[step_name] = step_data
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)

    def list_tasks(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        """列出所有任务。"""
        return db.list_entries(limit=limit, offset=offset)

    def delete_task(self, task_id: str) -> bool:
        """删除任务及其所有文件。"""
        entry = db.get_entry(task_id)
        if entry is None:
            return False

        task_dir = Path(entry["task_dir"])
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)

        return db.delete_entry(task_id)

    def get_upload_path(self, task_id: str) -> Path:
        """获取任务的上传目录。"""
        return self._tasks_dir / task_id / "uploads"

    def get_task_dir(self, task_id: str) -> Path:
        """获取任务根目录。"""
        return self._tasks_dir / task_id

    def active_count(self) -> int:
        """获取正在运行的任务数。"""
        running_statuses = {
            TASK_STATUS["VISUAL_RUNNING"],
            TASK_STATUS["SPEECH_RUNNING"],
            TASK_STATUS["GESTURE_RUNNING"],
            TASK_STATUS["SCORING"],
            TASK_STATUS["REPORTING"],
        }
        count = 0
        for t in db.list_entries():
            if t.get("status") in running_statuses:
                count += 1
        return count

    def submit_task(self, task_id: str, video_path: str) -> None:
        """将任务提交到后台线程池执行。"""
        from server.services.pipeline_runner import PipelineRunner

        task_dir = self.get_task_dir(task_id)
        runner = PipelineRunner(task_id, video_path, str(task_dir))

        def _run():
            try:
                result = runner.run()
                logger.info("任务 %s 完成: %s", task_id, result.get("status"))
            except Exception as exc:
                logger.error("任务 %s 异常: %s", task_id, exc)

        future = self._executor.submit(_run)
        logger.info("任务 %s 已提交到后台队列 (活跃任务: %d)", task_id, self.active_count() + 1)
