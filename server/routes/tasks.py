"""
任务 REST API 端点
"""
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from server.config import MAX_UPLOAD_SIZE, TASK_STATUS
from server.services.task_manager import get_manager

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("")
async def upload_video(file: UploadFile = File(...)):
    """上传视频，创建分析任务并自动启动分析。"""
    # 校验文件类型
    allowed_ext = {".mp4", ".mov", ".avi", ".webm"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed_ext:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext}。支持: {', '.join(allowed_ext)}",
        )

    # 校验大小
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大 ({len(content) / 1024 / 1024:.1f} MB)。最大 {MAX_UPLOAD_SIZE / 1024 / 1024:.0f} MB",
        )

    # 创建任务
    manager = get_manager()
    task = manager.create_task(file.filename)

    # 保存上传文件
    upload_dir = manager.get_upload_path(task["id"])
    video_path = upload_dir / f"video{ext}"
    with open(video_path, "wb") as f:
        f.write(content)

    # 更新状态
    manager.update_status(task["id"], TASK_STATUS["UPLOADED"])

    # 启动后台分析任务
    manager.submit_task(task["id"], str(video_path))

    return JSONResponse({
        "task_id": task["id"],
        "status": TASK_STATUS["UPLOADED"],
        "filename": file.filename,
        "size_bytes": len(content),
    })


@router.get("")
async def list_tasks(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    """列出所有任务。"""
    manager = get_manager()
    tasks = manager.list_tasks(limit=limit, offset=offset)
    return JSONResponse(tasks)


@router.get("/{task_id}")
async def get_task(task_id: str):
    """获取单个任务详情。"""
    manager = get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return JSONResponse(task)


@router.get("/{task_id}/status")
async def get_task_status(task_id: str):
    """获取任务状态。"""
    manager = get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return JSONResponse({
        "task_id": task_id,
        "status": task.get("status"),
        "steps": task.get("steps", {}),
        "timing": task.get("timing", {}),
    })


@router.get("/{task_id}/features")
async def get_task_features(task_id: str):
    """获取任务特征数据。"""
    manager = get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    features_dir = manager.get_task_dir(task_id) / "features"
    features = {}
    for name in ["visual", "speech", "gesture"]:
        for f in features_dir.glob(f"{name}_features_*.json"):
            import json
            with open(f, "r", encoding="utf-8") as fh:
                features[name] = json.load(fh)
    return JSONResponse(features)


@router.get("/{task_id}/score")
async def get_task_score(task_id: str):
    """获取任务评分。"""
    manager = get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    reports_dir = manager.get_task_dir(task_id) / "reports"
    for f in reports_dir.glob("final_score_*.json"):
        import json
        with open(f, "r", encoding="utf-8") as fh:
            return JSONResponse(json.load(fh))
    raise HTTPException(status_code=404, detail="评分尚未生成")


@router.get("/{task_id}/report")
async def get_task_report(task_id: str):
    """获取任务报告。"""
    manager = get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    reports_dir = manager.get_task_dir(task_id) / "reports"
    for f in reports_dir.glob("speech_report_*.md"):
        return PlainTextResponse(
            f.read_text(encoding="utf-8"),
            media_type="text/markdown; charset=utf-8",
        )
    raise HTTPException(status_code=404, detail="报告尚未生成")


@router.get("/{task_id}/confidence")
async def get_task_confidence(task_id: str):
    """获取任务可信度报告。"""
    manager = get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    reports_dir = manager.get_task_dir(task_id) / "reports"
    conf_path = reports_dir / "confidence_report.json"
    if conf_path.exists():
        import json
        with open(conf_path, "r", encoding="utf-8") as f:
            return JSONResponse(json.load(f))
    raise HTTPException(status_code=404, detail="可信度报告尚未生成")


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    """删除任务。"""
    manager = get_manager()
    ok = manager.delete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return JSONResponse({"deleted": True})
