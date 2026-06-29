"""
语音分析器模块
使用 ffmpeg 提取音频，whisper 转录，librosa 分析音高/音量特征。
仅CPU推理，离线批处理模式。
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# 可选依赖：资源监控
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

logger = logging.getLogger("speech_analyzer")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_handler)

# 模块级配置缓存
_CONFIG_LOADED = False
_SA_CFG: Dict = {}
_SP_THRESHOLDS: Dict = {}
_FILLER_WORDS: List[str] = []

# ── 诊断：阶段追踪 ──
_LAST_STAGE = "未开始"
_STAGE_TIMES: Dict[str, Dict[str, float]] = {}  # {step_name: {"start": ts, "end": ts, "cost": s}}

# ── 诊断：资源监控 ──
_MONITOR_STOP = threading.Event()
_MONITOR_THREAD = None
_ANALYSIS_START_TIME = 0.0

# ── 性能优化：Whisper 模型单例缓存 ──
_WHISPER_MODEL = None
_WHISPER_MODEL_NAME = None
_WHISPER_LOCK = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# 诊断辅助函数
# ══════════════════════════════════════════════════════════════════════════════

def _diag_stage_start(name: str):
    """记录阶段开始。"""
    global _LAST_STAGE, _STAGE_TIMES
    _LAST_STAGE = name
    ts = time.time()
    _STAGE_TIMES[name] = {"start": ts, "end": 0.0, "cost": 0.0}
    logger.info("[DIAG] STEP=%s START=%.3f", name, ts)


def _diag_stage_end(name: str):
    """记录阶段结束。"""
    global _LAST_STAGE, _STAGE_TIMES
    ts = time.time()
    if name in _STAGE_TIMES:
        start = _STAGE_TIMES[name]["start"]
        cost = ts - start
        _STAGE_TIMES[name]["end"] = ts
        _STAGE_TIMES[name]["cost"] = cost
        logger.info("[DIAG] STEP=%s END=%.3f COST=%.3f", name, ts, cost)
    else:
        logger.info("[DIAG] STEP=%s END=%.3f (无对应START记录)", name, ts)


def _resource_monitor_loop():
    """后台线程：每 10 秒打印资源状态。"""
    pid = os.getpid()
    start = time.time()
    while not _MONITOR_STOP.wait(10.0):  # 每 10 秒检查一次
        elapsed = time.time() - start
        rss_mb = "N/A"
        if _HAS_PSUTIL:
            try:
                proc = psutil.Process(pid)
                rss_mb = f"{proc.memory_info().rss / (1024 * 1024):.1f}"
            except Exception:
                pass
        logger.info("[DIAG] RESOURCE stage=%s pid=%s elapsed=%.0fs rss=%sMB",
                     _LAST_STAGE, pid, elapsed, rss_mb)


def _start_monitor():
    """启动资源监控后台线程。"""
    global _MONITOR_THREAD, _MONITOR_STOP
    _MONITOR_STOP.clear()
    _MONITOR_THREAD = threading.Thread(target=_resource_monitor_loop, daemon=True)
    _MONITOR_THREAD.start()


def _stop_monitor():
    """停止资源监控后台线程。"""
    global _MONITOR_THREAD
    _MONITOR_STOP.set()
    if _MONITOR_THREAD is not None:
        _MONITOR_THREAD.join(timeout=2)


def _print_diagnostic_summary():
    """输出最终诊断摘要。"""
    if not _STAGE_TIMES:
        return

    logger.info("[DIAG] ╔══════════════════════════════════════╗")
    logger.info("[DIAG] ║       最终诊断摘要                    ║")
    logger.info("[DIAG] ╚══════════════════════════════════════╝")

    # 按耗时排序
    sorted_stages = sorted(_STAGE_TIMES.items(), key=lambda x: x[1]["cost"], reverse=True)

    logger.info("[DIAG] 各阶段耗时排名 (从长到短):")
    max_cost_step = None
    max_cost = 0.0
    for i, (name, info) in enumerate(sorted_stages, 1):
        cost = info["cost"]
        if cost > max_cost:
            max_cost = cost
            max_cost_step = name
        marker = " ← 最长" if i == 1 else ""
        over_60 = " ⚠️ 超过60秒!" if cost > 60 else ""
        logger.info("[DIAG]   %d. %-40s COST=%.2f 秒%s%s", i, name, cost, marker, over_60)

    # 判断是否卡死
    logger.info("[DIAG] ──────────────────────────────────")
    if max_cost > 60 and max_cost_step:
        logger.info("[DIAG] 判定: 存在超过 60 秒的阶段")
        logger.info("[DIAG] 最长阻塞点: %s (耗时 %.2f 秒)", max_cost_step, max_cost)

        # 精确指出阻塞函数
        if "whisper" in max_cost_step.lower() and "transcribe" in max_cost_step.lower():
            logger.info("[DIAG] 阻塞函数: whisper.model.transcribe() — CPU 推理瓶颈")
        elif "whisper" in max_cost_step.lower() and "load" in max_cost_step.lower():
            logger.info("[DIAG] 阻塞函数: whisper.load_model() — 模型文件 I/O 瓶颈")
        elif "ffmpeg" in max_cost_step.lower():
            logger.info("[DIAG] 阻塞函数: subprocess.run(ffmpeg) — 音频提取超时或 I/O 瓶颈")
        elif "librosa" in max_cost_step.lower() and "load" in max_cost_step.lower():
            logger.info("[DIAG] 阻塞函数: librosa.load() — 音频解码瓶颈")
        elif "pitch" in max_cost_step.lower():
            logger.info("[DIAG] 阻塞函数: librosa.pyin() — 基频提取计算瓶颈")
        elif "volume" in max_cost_step.lower():
            logger.info("[DIAG] 阻塞函数: librosa.feature.rms() — 音量特征提取瓶颈")
        else:
            logger.info("[DIAG] 阻塞函数: 参见阶段名 %s", max_cost_step)
    else:
        logger.info("[DIAG] 判定: 无单阶段超过 60 秒，非卡死")

    # 总耗时
    total = sum(v["cost"] for v in _STAGE_TIMES.values())
    logger.info("[DIAG] 各阶段累计耗时: %.2f 秒", total)
    logger.info("[DIAG] ══════════════════════════════════════")


# ══════════════════════════════════════════════════════════════════════════════
# 业务函数（仅加诊断日志，不改逻辑）
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_config():
    """按需加载配置（延迟导入 yaml 以加快模块加载）。"""
    global _CONFIG_LOADED, _SA_CFG, _SP_THRESHOLDS, _FILLER_WORDS
    if _CONFIG_LOADED:
        return

    import yaml

    config_dir = Path(__file__).resolve().parent.parent / "config"
    yaml_path = config_dir / "scoring_config.yaml"
    json_path = config_dir / "thresholds_config.json"

    if not yaml_path.exists():
        logger.error("配置文件缺失: %s，程序将退出", yaml_path)
        sys.exit(1)
    if not json_path.exists():
        logger.error("配置文件缺失: %s，程序将退出", json_path)
        sys.exit(1)

    with open(yaml_path, "r", encoding="utf-8") as f:
        yaml_cfg = yaml.safe_load(f)
    with open(json_path, "r", encoding="utf-8") as f:
        json_cfg = json.load(f)

    _SA_CFG = yaml_cfg.get("speech_analyzer", {})
    _SP_THRESHOLDS = json_cfg.get("speech", {})
    _FILLER_WORDS = _SA_CFG.get("filler_words", [])
    _CONFIG_LOADED = True


def _get_ffmpeg_exe() -> str:
    """获取 ffmpeg 可执行文件路径。优先系统 PATH，其次 imageio-ffmpeg 内嵌。"""
    import shutil
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def _extract_audio(video_path: str, output_audio_path: str) -> bool:
    """使用 ffmpeg 从视频中提取 16kHz 单声道 WAV 音频。"""
    global _LAST_STAGE
    _LAST_STAGE = "ffmpeg: 获取可执行文件"

    ffmpeg_exe = _get_ffmpeg_exe()
    logger.info("[DIAG] ffmpeg 路径: %s", ffmpeg_exe)

    cmd = [
        ffmpeg_exe,
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(_SA_CFG.get("audio_sample_rate", 16000)),
        "-ac", str(_SA_CFG.get("audio_channels", 1)),
        "-y",
        output_audio_path,
    ]
    logger.info("[DIAG] FFmpeg 实际执行命令: %s", " ".join(cmd))
    logger.info("[DIAG] FFmpeg 输入视频: %s (大小=%s 字节)", video_path,
                os.path.getsize(video_path) if os.path.isfile(video_path) else "文件不存在")

    _LAST_STAGE = "ffmpeg: 执行中"
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
    except FileNotFoundError:
        logger.error("[DIAG] ffmpeg 未安装或不在 PATH 中: %s", ffmpeg_exe)
        _LAST_STAGE = "ffmpeg: FileNotFoundError"
        return False
    except subprocess.TimeoutExpired:
        logger.error("[DIAG] FFmpeg 提取音频超时 (超过120秒)")
        _LAST_STAGE = "ffmpeg: TimeoutExpired"
        return False
    except Exception as exc:
        logger.error("[DIAG] FFmpeg 子进程异常: %s", exc)
        _LAST_STAGE = f"ffmpeg: 异常 — {exc}"
        return False

    stdout_str = result.stdout.decode("utf-8", errors="replace")
    stderr_str = result.stderr.decode("utf-8", errors="replace")

    logger.info("[DIAG] FFmpeg returncode: %d", result.returncode)
    logger.info("[DIAG] FFmpeg stdout (前500字符): %s", stdout_str[:500] if stdout_str else "(空)")
    logger.info("[DIAG] FFmpeg stderr (前500字符): %s", stderr_str[:500] if stderr_str else "(空)")

    if result.returncode != 0:
        logger.error("[DIAG] FFmpeg 提取音频失败: returncode=%d, stderr=%s",
                     result.returncode, stderr_str[:300])
        return False

    audio_exists = os.path.isfile(output_audio_path)
    audio_size = os.path.getsize(output_audio_path) if audio_exists else 0
    logger.info("[DIAG] FFmpeg 输出 wav 路径: %s", output_audio_path)
    logger.info("[DIAG] FFmpeg 输出 wav 是否存在: %s", audio_exists)
    logger.info("[DIAG] FFmpeg 输出 wav 大小: %d Byte (%.2f MB)",
                 audio_size, audio_size / (1024 * 1024))

    if not audio_exists or audio_size == 0:
        logger.error("[DIAG] 音频文件不存在或为空: %s", output_audio_path)
        return False

    return True


def preload_whisper(model_name: Optional[str] = None):
    """预加载 Whisper 模型到模块级缓存（服务启动时调用一次）。

    CLI 模式若未调用此函数，_transcribe_with_whisper 会走原有的 lazy load 路径。
    """
    global _WHISPER_MODEL, _WHISPER_MODEL_NAME
    _ensure_config()
    if model_name is None:
        model_name = _SA_CFG.get("whisper_model", "tiny")

    try:
        import whisper
    except ImportError:
        logger.warning("[DIAG] openai-whisper 未安装，无法预加载模型")
        return

    with _WHISPER_LOCK:
        if _WHISPER_MODEL is not None:
            logger.info("[DIAG] Whisper 模型已缓存 (%s)，跳过预加载", _WHISPER_MODEL_NAME)
            return

        logger.info("[DIAG] Whisper 模型预加载开始: %s", model_name)
        t0 = time.time()
        _WHISPER_MODEL = whisper.load_model(model_name)
        _WHISPER_MODEL_NAME = model_name
        logger.info("[DIAG] Whisper 模型预加载完成: 耗时=%.2f秒", time.time() - t0)


def _transcribe_with_whisper(audio_path: str) -> Tuple[str, List[Dict]]:
    """使用 whisper 模型转录音频。

    优先使用模块级缓存的单例模型，避免重复 I/O。
    """
    global _LAST_STAGE, _WHISPER_MODEL, _WHISPER_MODEL_NAME
    if not os.path.exists(audio_path):
        logger.error("[DIAG] 音频文件不存在: %s", audio_path)
        _LAST_STAGE = "whisper: 音频文件不存在"
        return "语音转写暂不可用", []

    try:
        import whisper
    except ImportError:
        logger.warning("[DIAG] openai-whisper 未安装，返回默认转录文本")
        _LAST_STAGE = "whisper: 库未安装"
        return "语音转写暂不可用", []

    model_name = _SA_CFG.get("whisper_model", "tiny")
    logger.info("[DIAG] Whisper 使用模型名称: %s", model_name)

    # ── 模型缓存信息 ──
    try:
        _model_cache = os.path.join(os.path.expanduser("~"), ".cache", "whisper")
        _model_file = os.path.join(_model_cache, f"{model_name}.pt")
        if os.path.isfile(_model_file):
            logger.info("[DIAG] Whisper 模型缓存路径: %s (大小=%d Byte)", _model_file, os.path.getsize(_model_file))
        else:
            logger.info("[DIAG] Whisper 模型缓存路径: %s (文件不存在)", _model_file)
    except Exception:
        pass

    # ── 优先使用缓存单例 ──
    with _WHISPER_LOCK:
        if _WHISPER_MODEL is not None and _WHISPER_MODEL_NAME == model_name:
            model = _WHISPER_MODEL
            logger.info("[DIAG] Whisper 使用缓存单例模型: %s", model_name)
            _LAST_STAGE = f"whisper: 已使用缓存模型 {model_name}"
        else:
            # 缓存未命中 → 按需加载
            _diag_stage_start("whisper_load_model")
            _LAST_STAGE = f"whisper: load_model({model_name}) 缓存未命中，按需加载"
            logger.info("[DIAG] Whisper 缓存未命中，按需加载: %s", model_name)
            try:
                model = whisper.load_model(model_name)
                # 加载成功后写入缓存
                _WHISPER_MODEL = model
                _WHISPER_MODEL_NAME = model_name
            except Exception as exc:
                _diag_stage_end("whisper_load_model")
                logger.error("[DIAG] Whisper load_model 失败: %s", exc)
                import traceback
                logger.error("[DIAG] Whisper load_model traceback:\n%s", traceback.format_exc())
                _LAST_STAGE = f"whisper: load_model 失败 — {exc}"
                return "语音转写暂不可用", []
            _diag_stage_end("whisper_load_model")

    # ── 转录 ──
    _diag_stage_start("whisper_transcribe")
    _LAST_STAGE = "whisper: transcribe() 执行中"
    audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    logger.info("[DIAG] Whisper transcribe 开始时间: %s", datetime.now().strftime("%H:%M:%S.%f")[:-3])
    logger.info("[DIAG] Whisper transcribe 输入: 音频=%.2f MB, language=zh, fp16=False", audio_size_mb)
    try:
        result = model.transcribe(audio_path, language="zh", fp16=False)
    except Exception as exc:
        _diag_stage_end("whisper_transcribe")
        logger.error("[DIAG] Whisper transcribe 抛异常: %s", exc)
        import traceback
        logger.error("[DIAG] Whisper transcribe traceback:\n%s", traceback.format_exc())
        _LAST_STAGE = f"whisper: transcribe 异常 — {exc}"
        return "语音转写暂不可用", []
    _diag_stage_end("whisper_transcribe")

    logger.info("[DIAG] Whisper transcribe 返回时间: %s", datetime.now().strftime("%H:%M:%S.%f")[:-3])

    transcription = result.get("text", "").strip()
    segments = result.get("segments", [])
    logger.info("[DIAG] Whisper transcribe 返回文本长度: %d 字符", len(transcription))
    logger.info("[DIAG] Whisper transcribe 返回 segment 数量: %d", len(segments))
    logger.info("[DIAG] Whisper 转录文本 (前200字符): %s",
                 transcription[:200] if transcription else "(空)")

    if not transcription:
        logger.warning("[DIAG] 转录结果为空，使用默认文本")
        transcription = "语音转写暂不可用"

    _LAST_STAGE = "whisper: transcribe 完成"
    return transcription, segments


def _extract_pitch_features(audio_path: str, duration: float) -> Dict:
    """使用 librosa 提取基频（pitch）特征。"""
    global _LAST_STAGE
    try:
        import librosa
    except ImportError:
        logger.warning("librosa 未安装，返回默认音高特征")
        return {
            "pitch_mean_hz": 0.0, "pitch_std_hz": 0.0, "pitch_range_hz": 0.0,
            "pitch_variation_score": 0.0, "voiced_ratio": 0.0,
        }

    try:
        _diag_stage_start("librosa_load_for_pitch")
        _LAST_STAGE = "pitch: librosa.load()"
        y, sr = librosa.load(audio_path, sr=16000, mono=True)
        _diag_stage_end("librosa_load_for_pitch")

        fmin = _SA_CFG.get("pitch_fmin_hz", 50.0)
        fmax = _SA_CFG.get("pitch_fmax_hz", 500.0)

        _diag_stage_start("pitch_extract_pyin")
        _LAST_STAGE = "pitch: librosa.pyin()"
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y, fmin=fmin, fmax=fmax, sr=sr, fill_na=0.0,
        )
        _diag_stage_end("pitch_extract_pyin")

        voiced_f0 = f0[voiced_flag] if voiced_flag is not None and voiced_flag.any() else np.array([])
        voiced_ratio = float(voiced_flag.mean()) if voiced_flag is not None else 0.0

        if len(voiced_f0) == 0:
            return {
                "pitch_mean_hz": 0.0, "pitch_std_hz": 0.0, "pitch_range_hz": 0.0,
                "pitch_variation_score": 0.0, "voiced_ratio": voiced_ratio,
            }

        pitch_mean = float(np.mean(voiced_f0))
        pitch_std = float(np.std(voiced_f0))
        pitch_range = float(np.max(voiced_f0) - np.min(voiced_f0))
        ideal_std = 60.0
        pitch_variation_score = min(100.0, (pitch_std / ideal_std) * 100.0)
        pitch_variation_score = max(0.0, pitch_variation_score)

        return {
            "pitch_mean_hz": pitch_mean, "pitch_std_hz": pitch_std,
            "pitch_range_hz": pitch_range, "pitch_variation_score": pitch_variation_score,
            "voiced_ratio": voiced_ratio,
        }
    except Exception as exc:
        logger.warning("音高提取异常: %s", exc)
        import traceback as _tb
        logger.warning("[DIAG] pitch traceback:\n%s", _tb.format_exc())
        return {
            "pitch_mean_hz": 0.0, "pitch_std_hz": 0.0, "pitch_range_hz": 0.0,
            "pitch_variation_score": 0.0, "voiced_ratio": 0.0,
        }


def _extract_volume_features(audio_path: str) -> Dict:
    """提取音量特征（RMS 能量）。"""
    global _LAST_STAGE
    try:
        import librosa
    except ImportError:
        return {"rms_mean": 0.0, "rms_std": 0.0, "volume_variation_score": 0.0}

    try:
        _diag_stage_start("librosa_load_for_volume")
        _LAST_STAGE = "volume: librosa.load()"
        y, sr = librosa.load(audio_path, sr=16000, mono=True)
        _diag_stage_end("librosa_load_for_volume")

        rms = librosa.feature.rms(y=y)[0]
        rms_mean = float(np.mean(rms))
        rms_std = float(np.std(rms))

        ideal_cv = 0.3
        cv = rms_std / rms_mean if rms_mean > 1e-8 else 0.0
        volume_variation_score = max(0.0, min(100.0, (cv / ideal_cv) * 100.0))

        return {"rms_mean": rms_mean, "rms_std": rms_std, "volume_variation_score": volume_variation_score}
    except Exception as exc:
        logger.warning("音量特征提取异常: %s", exc)
        import traceback as _tb
        logger.warning("[DIAG] volume traceback:\n%s", _tb.format_exc())
        return {"rms_mean": 0.0, "rms_std": 0.0, "volume_variation_score": 0.0}


def _count_filler_words(text: str) -> Tuple[int, Dict[str, int], float]:
    """统计填充词出现次数。"""
    detected: Dict[str, int] = {}
    total_fillers = 0
    for fw in _FILLER_WORDS:
        count = text.count(fw)
        if count > 0:
            detected[fw] = count
            total_fillers += count

    chinese_chars = sum(1 for c in text if "一" <= c <= "鿿")
    english_words = len([w for w in text.split() if w.isascii() and w.isalpha()])
    total_words = max(chinese_chars + english_words, 1)
    ratio = total_fillers / total_words

    return total_fillers, detected, ratio


def _calculate_speech_rate(text: str, duration: float) -> float:
    """计算语速（词/分钟）。"""
    if duration <= 0:
        return 0.0
    chinese_chars = sum(1 for c in text if "一" <= c <= "鿿")
    english_words = len([w for w in text.split() if w.isascii() and w.isalpha()])
    total_words = chinese_chars + english_words
    return total_words / (duration / 60.0)


def _score_speech_rate(wpm: float) -> Tuple[float, float]:
    """根据语速计算评分。"""
    cfg = _SP_THRESHOLDS
    ideal_min = cfg.get("ideal_wpm_excellent_min", 130.0)
    ideal_max = cfg.get("ideal_wpm_excellent_max", 170.0)
    good_min = cfg.get("ideal_wpm_good_min", 110.0)
    good_max = cfg.get("ideal_wpm_good_max", 190.0)

    if ideal_min <= wpm <= ideal_max:
        return float(wpm), 100.0
    if good_min <= wpm <= good_max:
        if wpm < ideal_min:
            score = 70.0 + 30.0 * (wpm - good_min) / (ideal_min - good_min)
        else:
            score = 70.0 + 30.0 * (good_max - wpm) / (good_max - ideal_max)
        return float(wpm), score
    if wpm < good_min and good_min > 0:
        score = max(0.0, 70.0 * (wpm / good_min))
    elif good_max > 0:
        score = max(0.0, 70.0 * (good_max / wpm)) if wpm > 0 else 0.0
    else:
        score = 50.0
    return float(wpm), score


def _score_filler_words(ratio: float) -> float:
    """根据填充词比例计算评分。"""
    cfg = _SP_THRESHOLDS
    exc_max = cfg.get("filler_word_ratio_excellent_max", 0.02)
    good_max = cfg.get("filler_word_ratio_good_max", 0.05)

    if ratio <= exc_max:
        return 100.0
    if ratio <= good_max:
        return 70.0 + 30.0 * (good_max - ratio) / (good_max - exc_max)
    return max(0.0, 70.0 * (1.0 - (ratio - good_max)))


def _build_default_output(source: str) -> Dict:
    """构建默认回退输出。"""
    return {
        "source": source,
        "duration_seconds": 0.0,
        "transcription": "语音转写暂不可用",
        "word_count": 0,
        "sentence_count": 0,
        "speech_rate_features": {
            "words_per_minute": 0.0, "speech_rate_score": 0.0,
        },
        "pitch_features": {
            "pitch_mean_hz": 0.0, "pitch_std_hz": 0.0, "pitch_range_hz": 0.0,
            "pitch_variation_score": 0.0, "voiced_ratio": 0.0,
        },
        "volume_features": {
            "rms_mean": 0.0, "rms_std": 0.0, "volume_variation_score": 0.0,
        },
        "filler_word_analysis": {
            "filler_word_count": 0, "filler_word_ratio": 0.0,
            "filler_word_score": 0.0, "detected_fillers": {},
        },
        "dimension_scores": {"speech_score": 0.0},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 主分析函数
# ══════════════════════════════════════════════════════════════════════════════

def analyze(video_path: str, output_dir: Optional[str] = None) -> Dict:
    """分析视频中的语音特征。"""
    global _LAST_STAGE, _STAGE_TIMES, _ANALYSIS_START_TIME
    _LAST_STAGE = "开始"
    _STAGE_TIMES = {}
    _ANALYSIS_START_TIME = time.time()

    _ensure_config()
    logger.info("[DIAG] ══════════════════════════════════════")
    logger.info("[DIAG] 语音分析开始: %s", video_path)
    logger.info("[DIAG] Python PID: %d", os.getpid())

    # 启动资源监控
    _start_monitor()

    if not os.path.isfile(video_path):
        logger.warning("[DIAG] 视频文件不存在: %s", video_path)
        _LAST_STAGE = "视频文件不存在"
        _stop_monitor()
        output = _build_default_output(video_path)
        _save_output(output, output_dir)
        return output

    tmp_audio = None
    try:
        # ── 阶段 1: 打开视频 ──
        _diag_stage_start("01_open_video")
        _LAST_STAGE = "步骤1: 打开视频"
        vid_size = os.path.getsize(video_path)
        logger.info("[DIAG] 视频文件大小: %d Byte (%.2f MB)", vid_size, vid_size / (1024 * 1024))
        _diag_stage_end("01_open_video")

        # ── 阶段 2: 提取音频 (FFmpeg) ──
        _diag_stage_start("02_extract_audio_ffmpeg")
        _LAST_STAGE = "步骤2: 提取音频"
        tmp_fd, tmp_audio = tempfile.mkstemp(suffix=".wav", prefix="speech_")
        os.close(tmp_fd)
        logger.info("[DIAG] 临时音频文件: %s", tmp_audio)

        audio_ok = _extract_audio(video_path, tmp_audio)
        _diag_stage_end("02_extract_audio_ffmpeg")
        if not audio_ok:
            logger.warning("[DIAG] 音频提取失败，使用默认值")
            _LAST_STAGE = "音频提取失败 — 已回退"
            _stop_monitor()
            _print_diagnostic_summary()
            output = _build_default_output(video_path)
            _save_output(output, output_dir)
            return output

        # ── 阶段 3: 检查音频文件是否生成 ──
        _diag_stage_start("03_check_audio_file")
        _LAST_STAGE = "步骤3: 检查音频文件"
        wav_exists = os.path.isfile(tmp_audio)
        wav_size = os.path.getsize(tmp_audio) if wav_exists else 0
        logger.info("[DIAG] 音频文件检查: 存在=%s, 大小=%d Byte", wav_exists, wav_size)
        _diag_stage_end("03_check_audio_file")

        # ── 获取音频时长 ──
        _diag_stage_start("04_get_duration")
        _LAST_STAGE = "步骤4: 获取音频时长"
        try:
            import librosa
            duration = librosa.get_duration(path=tmp_audio)
            logger.info("[DIAG] 音频时长: %.2f 秒", duration)
        except Exception as exc:
            logger.error("[DIAG] 获取音频时长失败: %s", exc)
            duration = 0.0
        _diag_stage_end("04_get_duration")

        # ── 阶段 4+5+6: Whisper (load + transcribe — 在 _transcribe_with_whisper 内部已分别计时) ──
        _LAST_STAGE = "步骤5: Whisper 转录"
        transcription, segments = _transcribe_with_whisper(tmp_audio)

        # ── 阶段 7: librosa.load (在 pitch 提取内部已计时) ──
        # (pitch 提取会调用 librosa.load)

        # ── 阶段 8: pitch 提取 ──
        _diag_stage_start("08_pitch_extract_total")
        _LAST_STAGE = "步骤8: 音高提取"
        pitch_features = _extract_pitch_features(tmp_audio, duration)
        _diag_stage_end("08_pitch_extract_total")
        logger.info("[DIAG] pitch 结果: mean=%.1f Hz, std=%.1f Hz, voiced_ratio=%.2f",
                     pitch_features.get("pitch_mean_hz", 0),
                     pitch_features.get("pitch_std_hz", 0),
                     pitch_features.get("voiced_ratio", 0))

        # ── 阶段 9: volume 提取 ──
        _diag_stage_start("09_volume_extract")
        _LAST_STAGE = "步骤9: 音量提取"
        volume_features = _extract_volume_features(tmp_audio)
        _diag_stage_end("09_volume_extract")

        # ── 阶段 10: filler_count 统计 ──
        _diag_stage_start("10_filler_count")
        _LAST_STAGE = "步骤10: 填充词统计"
        filler_count, detected_fillers, filler_ratio = _count_filler_words(transcription)
        filler_score = _score_filler_words(filler_ratio)
        logger.info("[DIAG] 填充词: 总数=%d, 检测到=%s, 比例=%.4f, 得分=%.1f",
                     filler_count, str(detected_fillers), filler_ratio, filler_score)
        _diag_stage_end("10_filler_count")

        # ── 阶段 11: speech_rate 计算 ──
        _diag_stage_start("11_speech_rate")
        _LAST_STAGE = "步骤11: 语速计算"
        wpm = _calculate_speech_rate(transcription, duration)
        _, speech_rate_score = _score_speech_rate(wpm)
        logger.info("[DIAG] 语速: %.1f wpm, 得分=%.1f", wpm, speech_rate_score)
        _diag_stage_end("11_speech_rate")

        # ── 综合评分（不改逻辑） ──
        _LAST_STAGE = "综合评分计算"
        pw = _SP_THRESHOLDS.get("pitch_variation_weight", 0.35)
        sw = _SP_THRESHOLDS.get("speech_rate_weight", 0.35)
        fw = _SP_THRESHOLDS.get("filler_word_weight", 0.30)

        speech_score = (
            pw * pitch_features["pitch_variation_score"]
            + sw * speech_rate_score
            + fw * filler_score
        )
        speech_score = max(0.0, min(100.0, speech_score))

        chinese_chars = sum(1 for c in transcription if "一" <= c <= "鿿")
        english_words = len([w for w in transcription.split() if w.isascii() and w.isalpha()])
        word_count = chinese_chars + english_words
        sentence_count = max(1, transcription.count("。") + transcription.count("！") +
                             transcription.count("？") + transcription.count(".") +
                             transcription.count("!") + transcription.count("?"))

        output = {
            "source": video_path,
            "duration_seconds": duration,
            "transcription": transcription,
            "word_count": word_count,
            "sentence_count": sentence_count,
            "speech_rate_features": {
                "words_per_minute": float(wpm),
                "speech_rate_score": float(speech_rate_score),
            },
            "pitch_features": pitch_features,
            "volume_features": volume_features,
            "filler_word_analysis": {
                "filler_word_count": filler_count,
                "filler_word_ratio": float(filler_ratio),
                "filler_word_score": float(filler_score),
                "detected_fillers": detected_fillers,
            },
            "dimension_scores": {"speech_score": float(speech_score)},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # ── 阶段 12: JSON 写入 ──
        _diag_stage_start("12_json_write")
        _LAST_STAGE = "步骤12: JSON 写入"
        _save_output(output, output_dir)
        _diag_stage_end("12_json_write")

        # ── 完成 ──
        total_elapsed = time.time() - _ANALYSIS_START_TIME
        logger.info("[DIAG] 语音分析完成: 语速=%.1f wpm, 综合得分=%.1f, 总耗时=%.2f秒",
                     wpm, speech_score, total_elapsed)
        _LAST_STAGE = f"完成 — 总耗时 {total_elapsed:.1f}秒"
        _stop_monitor()
        _print_diagnostic_summary()
        return output

    except Exception as exc:
        total_elapsed = time.time() - _ANALYSIS_START_TIME
        logger.error("[DIAG] ══════════════════════════════════════")
        logger.error("[DIAG] 语音分析异常退出 (总耗时=%.2f秒)", total_elapsed)
        logger.error("[DIAG] 异常类型: %s", type(exc).__name__)
        logger.error("[DIAG] 异常信息: %s", exc)
        logger.error("[DIAG] 最后执行阶段: %s", _LAST_STAGE)
        import traceback
        logger.error("[DIAG] 完整 traceback:\n%s", traceback.format_exc())
        _LAST_STAGE = f"异常退出 — 最后阶段: {_LAST_STAGE}"
        _stop_monitor()
        _print_diagnostic_summary()
        output = _build_default_output(video_path)
        _save_output(output, output_dir)
        return output
    finally:
        if tmp_audio and os.path.isfile(tmp_audio):
            try:
                os.unlink(tmp_audio)
                logger.info("[DIAG] 已清理临时音频文件: %s", tmp_audio)
            except OSError:
                pass


def _save_output(features: Dict, output_dir: Optional[str] = None) -> str:
    """将特征字典写入 JSON 文件。"""
    global _LAST_STAGE
    _LAST_STAGE = "写入 speech_features.json"
    if output_dir is None:
        output_dir = str(Path(__file__).resolve().parent.parent / "output" / "features")
    os.makedirs(output_dir, exist_ok=True)

    source_name = Path(features["source"]).stem
    filename = f"speech_features_{source_name}.json"
    output_path = os.path.join(output_dir, filename)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(features, f, ensure_ascii=False, indent=2)

    file_size = os.path.getsize(output_path)
    logger.info("[DIAG] speech_features.json 写入完成: %s (大小=%d 字节)", output_path, file_size)
    _LAST_STAGE = "speech_features.json 已写入"
    return output_path


def get_last_stage() -> str:
    """返回 speech_analyzer 最后执行到的阶段，用于诊断卡顿。"""
    return _LAST_STAGE


def get_stage_times() -> Dict:
    """返回所有阶段的计时数据。"""
    return dict(_STAGE_TIMES)
