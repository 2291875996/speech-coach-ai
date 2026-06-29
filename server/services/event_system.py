"""
统一事件系统 (Event Backbone)
替换分散的 ProgressEmitter + QueueLogHandler，所有消息统一为 SpeechEvent。
"""
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set

# ═══════════════════════════════════════════════════════════════════════════
# Event Schema
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SpeechEvent:
    """统一事件模型，用于全链路追踪。"""
    event_id: str
    task_id: str
    trace_id: str
    stage: str          # visual | speech | gesture | scoring | report | calibration | system
    event_type: str     # STAGE_START | STAGE_END | PROGRESS | METRIC | ERROR | LOG | PIPELINE_DONE
    timestamp: float
    severity: str       # INFO | WARN | ERROR
    cost: float = 0.0   # 当前阶段已耗时 (秒)
    payload: Dict = field(default_factory=dict)
    seq: int = 0        # 单调递增事件序号，由 EventEmitter 分配

    def to_dict(self) -> Dict:
        d = asdict(self)
        # 移除空 payload
        if not d.get("payload"):
            d.pop("payload", None)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════
# EventEmitter — 单例事件总线
# ═══════════════════════════════════════════════════════════════════════════

class EventEmitter:
    """每任务一个实例的事件发射器。

    负责：
    1. 事件入队 (供 WebSocket 消费)
    2. 历史追溯 (最近 500 条)
    3. 广播到已连接的 WebSocket 客户端
    4. 磁盘持久化 (JSONL)
    """

    def __init__(self, task_id: str, traces_dir: str = ""):
        self.task_id = task_id
        self.trace_id = str(uuid.uuid4())[:8]
        self._queue: queue.Queue = queue.Queue(maxsize=500)
        self._history: List[SpeechEvent] = []
        self._history_lock = threading.Lock()
        self._ws_clients: Set[Any] = set()
        self._ws_lock = threading.Lock()
        self._traces_dir = traces_dir
        self._start_time = time.time()
        self._seq: int = 0          # 单调递增事件序号
        self._seq_lock = threading.Lock()  # _seq 线程安全锁（LogToEventBridge 可跨线程调用）

    # ── 核心 emit ──
    def emit(self, event: SpeechEvent) -> None:
        """推送事件到队列 + 记录历史 + 广播 WS。"""
        # 入队
        try:
            self._queue.put(event, timeout=1)
        except queue.Full:
            pass

        # 历史
        with self._history_lock:
            self._history.append(event)
            if len(self._history) > 500:
                self._history = self._history[-500:]

        # 广播到已连接的 WS 客户端
        with self._ws_lock:
            dead = []
            for ws in self._ws_clients:
                try:
                    # 使用 asyncio 不安全，改为标记发送
                    pass  # WS 端自己从 queue 拉取
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._ws_clients.discard(ws)

    # ── 快捷方法 ──
    def _mk(self, stage: str, event_type: str, severity: str = "INFO",
            cost: float = 0.0, payload: Dict = None) -> SpeechEvent:
        if cost == 0.0:
            cost = time.time() - self._start_time
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        evt = SpeechEvent(
            event_id=str(uuid.uuid4())[:8],
            task_id=self.task_id,
            trace_id=self.trace_id,
            stage=stage,
            event_type=event_type,
            timestamp=time.time(),
            severity=severity,
            cost=cost,
            payload=payload or {},
            seq=seq,
        )
        self.emit(evt)
        return evt

    def stage_start(self, stage: str, message: str = "") -> SpeechEvent:
        return self._mk(stage, "STAGE_START", payload={"message": message})

    def stage_end(self, stage: str, status: str = "ok", result: Dict = None,
                  message: str = "") -> SpeechEvent:
        return self._mk(stage, "STAGE_END", severity="INFO",
                        payload={"status": status, "result": result or {}, "message": message})

    def progress(self, stage: str, current: int = 0, total: int = 0,
                 message: str = "") -> SpeechEvent:
        pct = (current / total * 100) if total > 0 else 0
        return self._mk(stage, "PROGRESS",
                        payload={"current": current, "total": total, "percent": round(pct, 1), "message": message})

    def metric(self, stage: str, name: str, value: float, unit: str = "",
               message: str = "") -> SpeechEvent:
        return self._mk(stage, "METRIC",
                        payload={"name": name, "value": value, "unit": unit, "message": message})

    def error(self, stage: str, error_type: str, detail: str = "",
              recoverable: bool = True, stack_trace: str = "") -> SpeechEvent:
        return self._mk(stage, "ERROR", severity="ERROR",
                        payload={"error_type": error_type, "detail": detail,
                                 "recoverable": recoverable, "stack_trace": stack_trace})

    def log(self, level: str, message: str, module: str = "") -> SpeechEvent:
        return self._mk("system", "LOG", severity=level,
                        payload={"level": level, "message": message, "module": module})

    def pipeline_done(self, overall_score: float = 0, grade: str = "") -> SpeechEvent:
        return self._mk("system", "PIPELINE_DONE",
                        payload={"overall_score": overall_score, "grade": grade})

    # ── 控制事件 (STATUS / HEARTBEAT / STREAM_END) ──
    def status(self, status: str, overall_score: float = None, grade: str = "") -> SpeechEvent:
        return self._mk("system", "STATUS",
                        payload={"status": status, "overall_score": overall_score, "grade": grade})

    def heartbeat(self) -> SpeechEvent:
        return self._mk("system", "HEARTBEAT", payload={})

    def stream_end(self) -> SpeechEvent:
        return self._mk("system", "STREAM_END", payload={})

    # ── WebSocket 管理 ──
    def subscribe(self, ws: Any) -> None:
        with self._ws_lock:
            self._ws_clients.add(ws)

    def unsubscribe(self, ws: Any) -> None:
        with self._ws_lock:
            self._ws_clients.discard(ws)

    def get_queue(self) -> queue.Queue:
        return self._queue

    # ── 回溯 ──
    def replay(self) -> List[SpeechEvent]:
        """返回所有历史事件 (供新 WS 客户端同步状态)。"""
        with self._history_lock:
            return list(self._history)

    # ── 持久化 ──
    def flush_to_disk(self) -> Optional[str]:
        """将事件历史写入 JSONL 文件。"""
        if not self._traces_dir:
            return None
        import os
        os.makedirs(self._traces_dir, exist_ok=True)
        path = os.path.join(self._traces_dir, "events.jsonl")
        with self._history_lock:
            with open(path, "w", encoding="utf-8") as f:
                for evt in self._history:
                    f.write(evt.to_json() + "\n")
        return path


# ═══════════════════════════════════════════════════════════════════════════
# LogToEventBridge — 将 Python logging 自动转为 SpeechEvent
# ═══════════════════════════════════════════════════════════════════════════

class LogToEventBridge(logging.Handler):
    """logging.Handler 适配器：将传统 log record 转为 LOG 类型 SpeechEvent。

    挂载方式：
        bridge = LogToEventBridge(emitter)
        logging.getLogger("interview_analyzer").addHandler(bridge)
    """

    def __init__(self, emitter: EventEmitter):
        super().__init__()
        self.emitter = emitter
        self.setLevel(logging.INFO)

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            # 过滤 tqdm 噪声
            if any(kw in msg for kw in ["iB/s", "━━", "%|"]):
                return
            self.emitter.log(
                level=record.levelname,
                message=msg,
                module=record.name,
            )
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 全局 EventEmitter 注册表
# ═══════════════════════════════════════════════════════════════════════════

_EMITTERS: Dict[str, EventEmitter] = {}
_EMITTERS_LOCK = threading.Lock()


def register_emitter(task_id: str, emitter: EventEmitter) -> None:
    with _EMITTERS_LOCK:
        _EMITTERS[task_id] = emitter


def unregister_emitter(task_id: str) -> None:
    with _EMITTERS_LOCK:
        _EMITTERS.pop(task_id, None)


def get_emitter(task_id: str) -> Optional[EventEmitter]:
    with _EMITTERS_LOCK:
        return _EMITTERS.get(task_id)
