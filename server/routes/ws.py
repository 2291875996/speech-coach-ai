"""
WebSocket 端点 — 基于 EventEmitter 的实时事件流 (Phase 5)
所有消息统一信封: {"type": "event", "data": Event}
"""
import logging
import queue
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.config import PROTOCOL_VERSION, BUILD_ID
from server.services.event_system import get_emitter
from server.services.task_manager import get_manager

router = APIRouter()

WS_ENVELOPE = "event"


def _wrap(evt_dict: dict) -> dict:
    return {"type": WS_ENVELOPE, "data": evt_dict}


def _emit_control(emitter, event_type: str, **payload):
    if emitter is None:
        return None
    if event_type == "STATUS":
        return emitter.status(**payload)
    elif event_type == "HEARTBEAT":
        return emitter.heartbeat()
    elif event_type == "STREAM_END":
        return emitter.stream_end()
    elif event_type == "PIPELINE_DONE":
        return emitter.pipeline_done(**payload)
    return None


@router.websocket("/tasks/{task_id}")
async def websocket_task_events(websocket: WebSocket, task_id: str):
    manager = get_manager()
    task = manager.get_task(task_id)
    if task is None:
        await websocket.close(code=4004, reason="任务不存在")
        return

    await websocket.accept()

    # ── 0. 系统握手: 协议版本 + build ID ──
    await websocket.send_json(_wrap({
        "event_type": "SYSTEM_INIT",
        "task_id": task_id,
        "stage": "system",
        "severity": "INFO",
        "cost": 0,
        "timestamp": time.time(),
        "seq": 0,  # 握手事件，seq=0
        "payload": {
            "protocol_version": PROTOCOL_VERSION,
            "build_id": BUILD_ID,
        }
    }))

    emitter = get_emitter(task_id)

    # ── 1. Replay 历史事件 ──
    if emitter is not None:
        for evt in emitter.replay():
            try:
                await websocket.send_json(_wrap(evt.to_dict()))
            except Exception:
                logger = logging.getLogger("interview_analyzer.ws")
                logger.warning("WebSocket 历史重放中断 (task=%s)", task_id)
                return

    # ── 2. STATUS ──
    status_evt = _emit_control(emitter, "STATUS",
        status=task.get("status", "pending"),
        overall_score=task.get("overall_score"),
        grade=task.get("grade", ""))
    if status_evt:
        await websocket.send_json(_wrap(status_evt.to_dict()))

    # ── 3. 无 emitter → 发送完成并退出 ──
    if emitter is None:
        ts = time.time()
        st = task.get("status", "pending")
        if st == "completed":
            await websocket.send_json(_wrap({
                "event_type": "PIPELINE_DONE", "task_id": task_id,
                "stage": "system", "severity": "INFO", "cost": 0, "timestamp": ts, "seq": 9999,
                "payload": {"overall_score": task.get("overall_score", 0), "grade": task.get("grade", "")}
            }))
        await websocket.send_json(_wrap({
            "event_type": "STREAM_END", "task_id": task_id,
            "stage": "system", "severity": "INFO", "cost": 0, "timestamp": ts, "seq": 9999, "payload": {}
        }))
        return

    # ── 4. 实时推送 ──
    emitter.subscribe(websocket)
    evt_queue = emitter.get_queue()

    try:
        while True:
            try:
                evt = evt_queue.get(timeout=0.5)
                await websocket.send_json(_wrap(evt.to_dict()))
                if evt.event_type == "STREAM_END":
                    break
            except queue.Empty:
                hb = _emit_control(emitter, "HEARTBEAT")
                if hb:
                    await websocket.send_json(_wrap(hb.to_dict()))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger = logging.getLogger("interview_analyzer.ws")
        logger.error("WebSocket 事件循环异常 (task=%s): %s", task_id, exc, exc_info=True)
    finally:
        if emitter is not None:
            emitter.unsubscribe(websocket)
