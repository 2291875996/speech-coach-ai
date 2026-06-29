"""
run_test.py — AI演讲反馈教练 自动化测试脚本

功能:
  1. 生成 3 个合成测试视频
  2. 依次运行完整流水线
  3. 校验所有输出 JSON 的 Schema
  4. 输出 PASS / FAIL 报告

用法:
  python run_test.py                # 自动生成测试视频并运行
  python run_test.py <video_dir>    # 使用指定目录下的 .mp4 文件
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# 项目路径
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logger = logging.getLogger("run_test")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
logger.addHandler(_handler)


# ============================================================================
# Phase 1 — 合成测试视频生成
# ============================================================================

def _generate_synthetic_video(output_path: str, duration_sec: float = 3.0,
                              fps: int = 10, width: int = 320, height: int = 240,
                              pattern: str = "solid") -> str:
    """使用 OpenCV 生成合成测试视频。

    Args:
        output_path: 输出 .mp4 文件路径
        duration_sec: 时长（秒）
        fps: 帧率
        width / height: 分辨率
        pattern: "solid" | "checker" | "noise"

    Returns:
        视频文件路径
    """
    import cv2
    import numpy as np

    total_frames = int(duration_sec * fps)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

    for i in range(total_frames):
        if pattern == "checker":
            # 棋盘格 — MediaPipe 可能误检测到"面部"结构
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            sz = 40
            if (i // 3) % 2 == 0:
                for y in range(0, height, sz):
                    for x in range(0, width, sz):
                        if (x // sz + y // sz) % 2 == 0:
                            cv2.rectangle(frame, (x, y), (x + sz, y + sz),
                                          (200, 200, 200), -1)
        elif pattern == "noise":
            # 随机噪声
            frame = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
        else:
            # 纯色渐变
            t = i / max(total_frames - 1, 1)
            color = int(128 + 64 * (t - 0.5))
            frame = np.full((height, width, 3), (color, color, color), dtype=np.uint8)

        out.write(frame)

    out.release()
    return output_path


def _generate_synthetic_audio(output_path: str, duration_sec: float = 3.0,
                              sample_rate: int = 16000) -> str:
    """使用 Python 生成含正弦波的 WAV 音频（模拟人声频段）。

    Args:
        output_path: 输出 .wav 文件路径
        duration_sec: 时长（秒）
        sample_rate: 采样率

    Returns:
        音频文件路径
    """
    import wave
    import math
    import struct

    n_samples = int(duration_sec * sample_rate)
    with wave.open(output_path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(n_samples):
            t = i / sample_rate
            # 模拟语音频段：基频 ~150Hz + 谐波
            val = 0.6 * math.sin(2 * math.pi * 150 * t)
            val += 0.3 * math.sin(2 * math.pi * 300 * t)
            val += 0.1 * math.sin(2 * math.pi * 450 * t)
            # 振幅包络
            env = 1.0 - abs(2.0 * t / duration_sec - 1.0)
            sample = int(16000 * val * env)
            sample = max(-32768, min(32767, sample))
            wf.writeframes(struct.pack("<h", sample))
    return output_path


def _mux_video_audio(video_path: str, audio_path: str, output_path: str) -> str:
    """使用 ffmpeg 将视频和音频合并为带音轨的 MP4。"""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        output_path,
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # ffmpeg 不可用时，回退到无音频视频
        shutil.copy(video_path, output_path)
    return output_path


def prepare_test_videos(test_dir: str) -> List[str]:
    """生成 3 个合成测试视频到 test_dir，返回视频路径列表。

    视频1 — 纯色（简单场景）
    视频2 — 棋盘格（复杂纹理）
    视频3 — 随机噪声（极端场景）
    """
    os.makedirs(test_dir, exist_ok=True)
    videos: List[str] = []

    for idx, pattern in enumerate(["solid", "checker", "noise"], start=1):
        video_name = f"test_video_{idx:02d}.mp4"
        final_path = os.path.join(test_dir, video_name)

        # 检查是否已存在
        if os.path.exists(final_path):
            logger.info("测试视频已存在，跳过生成: %s", final_path)
            videos.append(final_path)
            continue

        with tempfile.TemporaryDirectory() as tmp:
            raw_video = os.path.join(tmp, "raw.mp4")
            raw_audio = os.path.join(tmp, "audio.wav")

            logger.info("生成测试视频 %d/3: pattern=%s ...", idx, pattern)
            _generate_synthetic_video(raw_video, duration_sec=3.0, pattern=pattern)
            _generate_synthetic_audio(raw_audio, duration_sec=3.0)
            _mux_video_audio(raw_video, raw_audio, final_path)

        videos.append(final_path)
        logger.info("测试视频已生成: %s", final_path)

    return videos


# ============================================================================
# Phase 2 — 流水线执行
# ============================================================================

def _run_pipeline(video_path: str, output_dir: str) -> Tuple[int, str, str]:
    """运行主流水线，返回 (exit_code, stdout, stderr)。"""
    main_py = str(_PROJECT_ROOT / "main.py")
    cmd = [
        sys.executable, main_py,
        video_path,
        "-o", output_dir,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return result.returncode, result.stdout, result.stderr


# ============================================================================
# Phase 3 — 输出校验
# ============================================================================

def _validate_outputs(output_dir: str, video_name: str) -> Dict:
    """校验单个视频流水线的全部输出。

    Returns:
        {
            "video": str,
            "exit_code": int,
            "checks": {
                "visual_json_exists": bool,
                "visual_schema_pass": bool,
                "speech_json_exists": bool,
                "speech_schema_pass": bool,
                "gesture_json_exists": bool,
                "gesture_schema_pass": bool,
                "score_json_exists": bool,
                "score_schema_pass": bool,
                "report_md_exists": bool,
            },
            "passed": int,
            "failed": int,
        }
    """
    from modules.schema_validator import (
        validate_visual_output,
        validate_speech_output,
        validate_gesture_output,
        validate_score_output,
    )

    features_dir = os.path.join(output_dir, "features")
    reports_dir = os.path.join(output_dir, "reports")
    stem = Path(video_name).stem

    checks = {}

    # --- 视觉特征 ---
    visual_path = os.path.join(features_dir, f"visual_features_{stem}.json")
    checks["visual_json_exists"] = os.path.isfile(visual_path)
    checks["visual_schema_pass"] = False
    if checks["visual_json_exists"]:
        try:
            with open(visual_path, "r", encoding="utf-8") as f:
                vf = json.load(f)
            checks["visual_schema_pass"] = validate_visual_output(vf)
        except Exception:
            pass

    # --- 语音特征 ---
    speech_path = os.path.join(features_dir, f"speech_features_{stem}.json")
    checks["speech_json_exists"] = os.path.isfile(speech_path)
    checks["speech_schema_pass"] = False
    if checks["speech_json_exists"]:
        try:
            with open(speech_path, "r", encoding="utf-8") as f:
                sf = json.load(f)
            checks["speech_schema_pass"] = validate_speech_output(sf)
        except Exception:
            pass

    # --- 手势特征 ---
    gesture_path = os.path.join(features_dir, f"gesture_features_{stem}.json")
    checks["gesture_json_exists"] = os.path.isfile(gesture_path)
    checks["gesture_schema_pass"] = False
    if checks["gesture_json_exists"]:
        try:
            with open(gesture_path, "r", encoding="utf-8") as f:
                gf = json.load(f)
            checks["gesture_schema_pass"] = validate_gesture_output(gf)
        except Exception:
            pass

    # --- 评分 JSON ---
    score_path = None
    if os.path.isdir(reports_dir):
        score_files = sorted(
            [f for f in os.listdir(reports_dir) if f.startswith("final_score_")],
            reverse=True,
        )
        if score_files:
            score_path = os.path.join(reports_dir, score_files[0])
    checks["score_json_exists"] = score_path is not None and os.path.isfile(score_path)
    checks["score_schema_pass"] = False
    if checks["score_json_exists"]:
        try:
            with open(score_path, "r", encoding="utf-8") as f:
                sc = json.load(f)
            checks["score_schema_pass"] = validate_score_output(sc)
        except Exception:
            pass

    # --- 报告 Markdown ---
    report_path = None
    if os.path.isdir(reports_dir):
        report_files = sorted(
            [f for f in os.listdir(reports_dir) if f.startswith("speech_report_")],
            reverse=True,
        )
        if report_files:
            report_path = os.path.join(reports_dir, report_files[0])
    checks["report_md_exists"] = report_path is not None and os.path.isfile(report_path)

    passed = sum(1 for v in checks.values() if v)
    failed = len(checks) - passed

    return {
        "video": video_name,
        "checks": checks,
        "passed": passed,
        "failed": failed,
    }


# ============================================================================
# Phase 4 — 报告
# ============================================================================

def _print_report(results: List[Dict]) -> int:
    """打印测试报告，返回 0=全部通过, 1=存在失败。"""
    total_checks = sum(r["passed"] + r["failed"] for r in results)
    total_passed = sum(r["passed"] for r in results)
    total_failed = sum(r["failed"] for r in results)

    print()
    print("=" * 60)
    print("  AI演讲反馈教练 — 自动化测试报告")
    print("  时间:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    check_labels = [
        ("visual_json_exists",   "视觉特征 JSON 存在"),
        ("visual_schema_pass",   "视觉特征 Schema 校验"),
        ("speech_json_exists",   "语音特征 JSON 存在"),
        ("speech_schema_pass",   "语音特征 Schema 校验"),
        ("gesture_json_exists",  "手势特征 JSON 存在"),
        ("gesture_schema_pass",  "手势特征 Schema 校验"),
        ("score_json_exists",    "评分 JSON 存在"),
        ("score_schema_pass",    "评分 Schema 校验"),
        ("report_md_exists",     "演讲反馈报告 Markdown 存在"),
    ]

    for idx, r in enumerate(results, start=1):
        print(f"\n--- 测试视频 {idx}: {r['video']} ---")
        for key, label in check_labels:
            status = "PASS" if r["checks"].get(key, False) else "FAIL"
            marker = "  PASS  " if status == "PASS" else "  FAIL  "
            print(f"  [{marker}] {label}")

    print(f"\n{'=' * 60}")
    print(f"  总计: {total_checks} 项检查")
    print(f"  通过: {total_passed}")
    print(f"  失败: {total_failed}")
    if total_failed == 0:
        print(f"  结果: ALL PASS")
    else:
        print(f"  结果: FAIL ({total_failed} 项未通过)")
    print(f"{'=' * 60}")

    return 0 if total_failed == 0 else 1


# ============================================================================
# 入口
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="AI演讲反馈教练 自动化测试脚本",
    )
    parser.add_argument(
        "video_dir",
        nargs="?",
        default=None,
        help="测试视频目录（含 .mp4 文件）。不指定则自动生成合成视频。",
    )
    parser.add_argument(
        "--keep-output",
        action="store_true",
        help="保留输出目录（默认测试后清理）",
    )
    args = parser.parse_args()

    # --- 准备测试视频 ---
    if args.video_dir and os.path.isdir(args.video_dir):
        videos = sorted([
            os.path.join(args.video_dir, f)
            for f in os.listdir(args.video_dir)
            if f.endswith(".mp4")
        ])
        if not videos:
            logger.error("指定目录中没有 .mp4 文件: %s", args.video_dir)
            sys.exit(1)
        logger.info("使用现有测试视频: %d 个", len(videos))
    else:
        if args.video_dir:
            logger.warning("目录不存在: %s，将自动生成测试视频", args.video_dir)
        test_dir = os.path.join(_PROJECT_ROOT, "output", "test_videos")
        videos = prepare_test_videos(test_dir)

    # --- 执行测试 ---
    results: List[Dict] = []
    base_output = os.path.join(_PROJECT_ROOT, "output", "test_runs")

    for idx, video_path in enumerate(videos, start=1):
        video_name = Path(video_path).name
        output_dir = os.path.join(base_output, f"run_{idx:02d}")
        logger.info("=" * 50)
        logger.info("测试 %d/%d: %s", idx, len(videos), video_name)
        logger.info("=" * 50)

        exit_code, stdout, stderr = _run_pipeline(video_path, output_dir)
        logger.info("流水线退出码: %d", exit_code)

        result = _validate_outputs(output_dir, video_name)
        result["exit_code"] = exit_code
        results.append(result)

    # --- 打印报告 ---
    report_exit = _print_report(results)

    # --- 清理 ---
    if not args.keep_output and os.path.isdir(base_output):
        shutil.rmtree(base_output, ignore_errors=True)
        logger.info("已清理测试输出目录: %s", base_output)

    sys.exit(report_exit)


if __name__ == "__main__":
    main()
