"""
流水线运行器
在后台线程中执行完整的分析流水线，通过 queue 推送实时进度。
Phase 3: 视觉/语音/手势三分析器并行执行。
"""
import concurrent.futures
import logging
import os
import queue
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from modules import visual_analyzer, speech_analyzer, gesture_analyzer
from modules import scoring_engine, report_generator, schema_validator
from server import db
from server.config import TASK_STATUS
from server.services.event_system import EventEmitter, LogToEventBridge, register_emitter, unregister_emitter
from server.services.task_manager import get_manager

logger = logging.getLogger("interview_analyzer.pipeline")


class PipelineRunner:
    """在后台线程中运行分析流水线，实时推送进度。

    Phase 3: 视觉/语音/手势 三个分析器并行执行（ThreadPoolExecutor）。
    三者独立：都只读取 video_path，写入各自的 features/*.json，
    无相互依赖，C 扩展（OpenCV/MediaPipe/Whisper/librosa）释放 GIL。
    """

    def __init__(self, task_id: str, video_path: str, output_dir: str):
        self.task_id = task_id
        self.video_path = video_path
        self.output_dir = output_dir
        self.features_dir = os.path.join(output_dir, "features")
        self.reports_dir = os.path.join(output_dir, "reports")
        self.logs_dir = os.path.join(output_dir, "logs")

        for d in [self.features_dir, self.reports_dir, self.logs_dir]:
            os.makedirs(d, exist_ok=True)

        # Phase 5: 统一事件系统
        self.events = EventEmitter(task_id, traces_dir=self.logs_dir)
        self._cancelled = False
        self._done_sent = False  # 防止 finally 中重复发送 PIPELINE_DONE

    def cancel(self):
        self._cancelled = True

    def run(self) -> Dict:
        """执行完整流水线（Phase 3: 阶段级并行）。"""
        manager = get_manager()
        t0 = time.time()
        timing = {}

        # ── 注册事件发射器 + 挂载日志桥 ──
        register_emitter(self.task_id, self.events)
        bridge = LogToEventBridge(self.events)
        bridge.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
        root_logger = logging.getLogger("interview_analyzer")
        root_logger.addHandler(bridge)
        for name in ["visual_analyzer", "speech_analyzer", "gesture_analyzer"]:
            logging.getLogger(name).addHandler(bridge)

        overall, grade = 0.0, ""
        try:
            # ═══════════════════════════════════════════════════════════
            # Phase 1 (并行): 视觉 + 语音 + 手势 同时执行
            # ═══════════════════════════════════════════════════════════
            if self._cancelled:
                return self._abort(manager)

            # 设置并行状态
            manager.update_status(self.task_id, TASK_STATUS["VISUAL_RUNNING"])
            self.events.stage_start("visual", "开始视觉分析（眼神交流 + 头部姿态）")
            self.events.stage_start("speech", "开始语音分析（转录 + 语速 + 音高 + 填充词）")
            self.events.stage_start("gesture", "开始手势分析（手部关键点检测 + 分类）")

            t_parallel = time.time()
            results = {}  # {name: (result_dict, error_str)}

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                futures = {}

                def _run_visual():
                    t = time.time()
                    try:
                        vf = visual_analyzer.analyze(self.video_path, output_dir=self.features_dir)
                        return ("visual", {"result": vf, "cost": time.time() - t, "error": None})
                    except Exception as exc:
                        return ("visual", {"result": None, "cost": time.time() - t, "error": str(exc)})

                def _run_speech():
                    t = time.time()
                    try:
                        sf = speech_analyzer.analyze(self.video_path, output_dir=self.features_dir)
                        return ("speech", {"result": sf, "cost": time.time() - t, "error": None})
                    except Exception as exc:
                        return ("speech", {"result": None, "cost": time.time() - t, "error": str(exc)})

                def _run_gesture():
                    t = time.time()
                    try:
                        gf = gesture_analyzer.analyze(self.video_path, output_dir=self.features_dir)
                        return ("gesture", {"result": gf, "cost": time.time() - t, "error": None})
                    except Exception as exc:
                        return ("gesture", {"result": None, "cost": time.time() - t, "error": str(exc)})

                futures[pool.submit(_run_visual)] = "visual"
                futures[pool.submit(_run_speech)] = "speech"
                futures[pool.submit(_run_gesture)] = "gesture"

                for future in concurrent.futures.as_completed(futures):
                    name, data = future.result()
                    results[name] = data
                    cost = data["cost"]
                    timing[name] = cost
                    error = data["error"]

                    if error:
                        logger.error("%s 分析失败: %s", name, error)
                        self.events.stage_end(name, status="failed",
                            result={}, message=f"{name} 分析失败: {error}")
                        manager.update_step(self.task_id, name, {"status": "error", "error": error})
                    else:
                        r = data["result"]
                        scores = r.get("dimension_scores", {})
                        self.events.stage_end(name, status="ok",
                            result=scores, message=f"{name} 分析完成 ({cost:.1f}s)")
                        manager.update_step(self.task_id, name, {
                            "status": "done", "cost": cost,
                            **{f"{k}": v for k, v in scores.items()},
                        })
                        # 校验
                        try:
                            if name == "visual":
                                schema_validator.validate_visual_output(r)
                            elif name == "speech":
                                schema_validator.validate_speech_output(r)
                            elif name == "gesture":
                                schema_validator.validate_gesture_output(r)
                        except Exception as vexc:
                            logger.warning("%s schema 校验未通过: %s", name, vexc)

            timing["parallel_phase"] = time.time() - t_parallel
            logger.info("并行分析阶段完成: 总耗时=%.1fs (视觉=%.1fs, 语音=%.1fs, 手势=%.1fs)",
                         timing["parallel_phase"],
                         timing.get("visual", 0),
                         timing.get("speech", 0),
                         timing.get("gesture", 0))

            # ═══════════════════════════════════════════════════════════
            # Phase 2 (顺序): 汇总校验 → 评分 → 报告
            # ═══════════════════════════════════════════════════════════
            src = Path(self.video_path).stem
            vp = os.path.join(self.features_dir, f"visual_features_{src}.json")
            sp = os.path.join(self.features_dir, f"speech_features_{src}.json")
            gp = os.path.join(self.features_dir, f"gesture_features_{src}.json")
            vp = vp if os.path.isfile(vp) else ""
            sp = sp if os.path.isfile(sp) else ""
            gp = gp if os.path.isfile(gp) else ""

            # ── 汇总校验 ──
            if self._cancelled:
                return self._abort(manager)
            if not schema_validator.validate_all_features(vp, sp, gp):
                logger.warning("汇总校验降级: 部分特征文件校验未通过")

            # ── 综合评分 ──
            manager.update_status(self.task_id, TASK_STATUS["SCORING"])
            self.events.stage_start("scoring", "综合评分计算中...")
            t_step = time.time()
            score_path = ""
            try:
                score_data = scoring_engine.compute(
                    visual_features_path=vp, speech_features_path=sp,
                    gesture_features_path=gp, output_dir=self.reports_dir)
                timing["scoring"] = time.time() - t_step
                overall = score_data.get("overall_score", 0)
                grade = score_data.get("grade", "")
                self.events.stage_end("scoring", status="ok",
                    result={"overall_score": overall, "grade": grade},
                    message=f"评分完成: {overall:.1f} 分 ({grade})")
                manager.update_step(self.task_id, "scoring", {
                    "status": "done", "cost": timing["scoring"],
                    "overall_score": overall, "grade": grade})
                db.update_entry(self.task_id, overall_score=overall, grade=grade,
                                dimension_scores=score_data.get("dimension_scores"))
                score_files = sorted(
                    [f for f in os.listdir(self.reports_dir) if f.startswith("final_score_")],
                    reverse=True)
                score_path = os.path.join(self.reports_dir, score_files[0]) if score_files else ""
                schema_validator.validate_score_output(score_data)
            except Exception as exc:
                timing["scoring"] = time.time() - t_step
                logger.error("综合评分失败: %s", exc)
                self.events.error("scoring", "ScoringError", str(exc))
                manager.update_step(self.task_id, "scoring", {"status": "error", "error": str(exc)})

            # ── Phase 6: 校准与可信度验证 ──
            if self._cancelled:
                return self._abort(manager)
            self.events.stage_start("calibration", "模型校准与可信度验证...")
            t_step = time.time()
            try:
                from modules.calibration import generate_authenticity_report, save_report
                calib = generate_authenticity_report(vp, sp, gp, score_path)
                save_report(calib, self.reports_dir)
                timing["calibration"] = time.time() - t_step
                self.events.metric("calibration", "overall_confidence", calib.overall_confidence, "0-1",
                                   f"可信度: {calib.overall_confidence:.0%}")
                self.events.stage_end("calibration", status="ok",
                    result={"overall_confidence": calib.overall_confidence},
                    message=f"校准完成 ({timing['calibration']:.1f}s)")
                manager.update_step(self.task_id, "calibration", {
                    "status": "done", "cost": timing["calibration"],
                    "overall_confidence": calib.overall_confidence})
            except Exception as exc:
                timing["calibration"] = time.time() - t_step
                logger.warning("校准步骤失败（非致命）: %s", exc)
                self.events.error("calibration", "CalibrationError", str(exc), recoverable=True)

            # ── 报告生成 ──
            if self._cancelled:
                return self._abort(manager)
            manager.update_status(self.task_id, TASK_STATUS["REPORTING"])
            self.events.stage_start("report", "生成中文分析报告...")
            t_step = time.time()
            try:
                report_generator.generate(
                    score_json_path=score_path, visual_features_path=vp,
                    speech_features_path=sp, gesture_features_path=gp,
                    output_dir=self.reports_dir)
                timing["report"] = time.time() - t_step
                self.events.stage_end("report", status="ok",
                    message=f"报告生成完成 ({timing['report']:.1f}s)")
                manager.update_step(self.task_id, "report", {"status": "done", "cost": timing["report"]})
            except Exception as exc:
                timing["report"] = time.time() - t_step
                logger.error("报告生成失败: %s", exc)
                self.events.error("report", "ReportError", str(exc))
                manager.update_step(self.task_id, "report", {"status": "error", "error": str(exc)})

            # ── 完成 ──
            total = time.time() - t0
            timing["total"] = total
            manager.update_status(
                self.task_id, TASK_STATUS["COMPLETED"],
                overall_score=overall, grade=grade, timing=timing)
            self.events.pipeline_done(overall_score=overall, grade=grade)
            self._done_sent = True  # 标记已发送，防止 finally 重复
            logger.info("流水线完成: 总分=%.1f, 等级=%s, 总耗时=%.1fs", overall, grade, total)

            return {"status": "completed", "overall_score": overall, "grade": grade, "timing": timing}

        except Exception as exc:
            total = time.time() - t0
            logger.error("流水线严重异常: %s\n%s", exc, traceback.format_exc())
            self.events.error("system", "PipelineFatal", str(exc), recoverable=False,
                              stack_trace=traceback.format_exc())
            manager.update_status(self.task_id, TASK_STATUS["FAILED"], error_message=str(exc))
            return {"status": "failed", "error": str(exc), "timing": {"total": total}}

        finally:
            # ── 仅在正常路径未发送 DONE 时补发（异常/取消路径）──
            if not self._cancelled and not self._done_sent:
                self.events.pipeline_done(overall_score=overall, grade=grade)
            # 统一流结束信号（必须是 STREAM_END event_type，前端和 WS 都依赖它）
            self.events.stream_end()
            # 持久化事件日志
            self.events.flush_to_disk()
            # 清理日志桥
            root_logger.removeHandler(bridge)
            for name in ["visual_analyzer", "speech_analyzer", "gesture_analyzer"]:
                logging.getLogger(name).removeHandler(bridge)
            unregister_emitter(self.task_id)

    def _abort(self, manager) -> Dict:
        manager.update_status(self.task_id, TASK_STATUS["FAILED"], error_message="任务被取消")
        self.events.error("system", "TaskCancelled", "任务被取消", recoverable=False)
        unregister_emitter(self.task_id)
        return {"status": "cancelled"}

    def is_alive(self) -> bool:
        """供外部轮询检查。"""
        return not self._cancelled
