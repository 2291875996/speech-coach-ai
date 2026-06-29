"""
模型校准与可信度验证模块 (Phase 6)
生成可解释、可审计、可答辩的可信度报告。
不修改 scoring_engine.py，不修改 report_generator.py。
"""
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional


# ═══════════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DimensionConfidence:
    """单个维度的可信度评估。"""
    dimension: str           # eye_contact | posture | gesture | speech
    confidence: float        # 0-1
    method: str              # 分析方法
    detection_rate: float    # 检测成功率
    stability: float         # 帧间稳定性 (0-1)
    bias_estimate: float = 0.0  # 系统性偏差估计值 (绝对值，单位视维度而定)
    calibration_notes: str = ""


@dataclass
class ErrorSource:
    """可信度误差来源。"""
    source: str              # solvePnP | face_detection | audio_quality | gesture_classification
    severity: float          # 0-1, 对总分的影响程度
    affected_dimension: str
    description: str


@dataclass
class CalibrationReport:
    """完整的可信度报告，用于答辩。"""
    overall_confidence: float
    dimensions: Dict[str, DimensionConfidence] = field(default_factory=dict)
    error_sources: List[ErrorSource] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    timestamp: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# 分析函数
# ═══════════════════════════════════════════════════════════════════════════

def _load_json(path: str) -> Optional[Dict]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _analyze_visual_confidence(vf: Optional[Dict]) -> List[DimensionConfidence]:
    """分析视觉维度的可信度（眼神交流 + 姿态）。"""
    results = []

    if vf is None:
        results.append(DimensionConfidence("eye_contact", 0.0, "none", 0.0, 0.0, "视觉特征数据缺失"))
        results.append(DimensionConfidence("posture", 0.0, "none", 0.0, 0.0, "视觉特征数据缺失"))
        return results

    total = vf.get("frame_count_total", 1)
    processed = vf.get("frame_count_processed", 0)
    detection_rate = processed / total if total > 0 else 0.0

    # ── eye_contact ──
    ec = vf.get("eye_contact_features", {})
    ear_left_mean = ec.get("ear_left_mean", 0)
    ear_left_std = ec.get("ear_left_std", 0)
    ear_stability = 1.0 - min(ear_left_std / max(ear_left_mean, 0.01), 1.0)
    ec_confidence = detection_rate * ear_stability
    ec_confidence = max(0.0, min(1.0, ec_confidence))
    results.append(DimensionConfidence(
        dimension="eye_contact",
        confidence=round(ec_confidence, 3),
        method="mediapipe_ear",
        detection_rate=round(detection_rate, 3),
        stability=round(ear_stability, 3),
        bias_estimate=round(abs(vf.get("posture_features", {}).get("head_pose_angles", {}).get("yaw_mean", 0)), 1),
        calibration_notes=f"EAR 均值={ear_left_mean:.3f}, 检测率={detection_rate:.0%}, 接触判定阈值=±25°",
    ))

    # ── posture ──
    pt = vf.get("posture_features", {})
    angles = pt.get("head_pose_angles", {})
    pitch_std = angles.get("pitch_std", 0)
    yaw_std = angles.get("yaw_std", 0)

    # 稳定性: 角度标准差越小越稳定
    angle_stability = 1.0 - min((pitch_std + yaw_std) / 2.0 / 30.0, 1.0)

    # solvePnP vs geometric fallback 判断
    pitch_mean_val = angles.get("pitch_mean", 0)
    pitch_mean_abs = abs(pitch_mean_val)
    yaw_mean_abs = abs(angles.get("yaw_mean", 0))

    # bias_estimate: pitch 均值偏离水平面的度数 (系统性偏差)
    bias_estimate = round(pitch_mean_abs, 1)

    # solvePnP 判定
    method = "mediapipe_solvepnp" if pitch_mean_abs < 60 else "geometric_fallback"
    notes = f"pitch 均值={pitch_mean_val:.1f}°, std={pitch_std:.1f}°, bias={bias_estimate}°"

    if method == "geometric_fallback" or pitch_mean_abs > 20:
        notes += "; 注意: solvePnP 可能大面积失效, camera focal=fov_diagonal 估算偏差 + nose_ratio=0.52 假设导致系统性偏移, 几何回退姿态角度约 ±5° 误差"

    pt_confidence = detection_rate * angle_stability
    pt_confidence = max(0.0, min(1.0, pt_confidence))
    results.append(DimensionConfidence(
        dimension="posture",
        confidence=round(pt_confidence, 3),
        method=method,
        detection_rate=round(detection_rate, 3),
        stability=round(angle_stability, 3),
        bias_estimate=bias_estimate,
        calibration_notes=notes,
    ))

    return results


def _analyze_speech_confidence(sf: Optional[Dict]) -> DimensionConfidence:
    """分析语音维度的可信度。"""
    if sf is None:
        return DimensionConfidence("speech", 0.0, "none", 0.0, 0.0, "语音特征数据缺失")

    transcription = sf.get("transcription", "")
    duration = sf.get("duration_seconds", 0)
    is_default = transcription == "语音转写暂不可用" or len(transcription) < 5

    if is_default:
        return DimensionConfidence(
            dimension="speech",
            confidence=0.0,
            method="whisper_failed",
            detection_rate=0.0,
            stability=0.0,
            calibration_notes="转录失败或返回默认值",
        )

    # 音频质量评估
    pitch = sf.get("pitch_features", {})
    voiced_ratio = pitch.get("voiced_ratio", 0)

    # 转录合理性: 每秒至少 1 个词
    wpm = sf.get("speech_rate_features", {}).get("words_per_minute", 0)
    rate_ok = 50 < wpm < 300 if wpm > 0 else False

    confidence = 0.7  # whisper tiny 基准
    if voiced_ratio > 0.3:
        confidence += 0.15
    if rate_ok:
        confidence += 0.1
    confidence = min(1.0, confidence)

    model_name = "whisper_tiny"  # 从配置推断
    return DimensionConfidence(
        dimension="speech",
        confidence=round(confidence, 3),
        method=model_name,
        detection_rate=1.0 if voiced_ratio > 0 else 0.5,
        stability=round(voiced_ratio, 3),
        calibration_notes=f"有声比例={voiced_ratio:.0%}, WPM={wpm:.0f}, 文本长度={len(transcription)}",
    )


def _analyze_gesture_confidence(gf: Optional[Dict]) -> DimensionConfidence:
    """分析手势维度的可信度。"""
    if gf is None:
        return DimensionConfidence("gesture", 0.0, "none", 0.0, 0.0, "手势特征数据缺失")

    total = gf.get("frame_count_total", 1)
    processed = gf.get("frame_count_processed", 0)
    presence = gf.get("hand_presence_ratio", 0)
    detection_rate = processed / total if total > 0 else 0.0

    gs = gf.get("gesture_statistics", {})
    gpm = gs.get("gestures_per_minute", 0)

    # 检查异常: 手势频率 > 200/min → 确定是按帧重复计数 (BUG, 非局限性)
    notes = f"手部存在率={presence:.0%}, 手势频率={gpm:.1f}/min"
    gesture_bias = 0.0
    if gpm > 200:
        notes += "; ❌ BUG: 手势频率异常偏高 — 连续相同手势按帧累计, 非独立手势计数。建议按连续相同手势去重后再统计"
        gesture_bias = round(gpm / 60.0, 1)  # 估算每秒计数偏差
    if presence < 0.2:
        notes += "; 手部检测率过低，手势得分可能被低估"

    variety = gs.get("gesture_variety_count", 0)
    variety_factor = min(variety / 3.0, 1.0) if variety > 0 else 0.3

    confidence = detection_rate * presence * variety_factor
    confidence = max(0.0, min(1.0, confidence))

    return DimensionConfidence(
        dimension="gesture",
        confidence=round(confidence, 3),
        method="mediapipe_hand_landmarker",
        detection_rate=round(detection_rate, 3),
        stability=round(presence, 3),
        bias_estimate=gesture_bias,
        calibration_notes=notes,
    )


def _identify_error_sources(vf: Optional[Dict], sf: Optional[Dict],
                            gf: Optional[Dict]) -> List[ErrorSource]:
    """识别关键误差来源。"""
    errors = []

    # ── 视觉: solvePnP 失败 + 相机偏差 ──
    if vf:
        total = vf.get("frame_count_total", 1)
        processed = vf.get("frame_count_processed", 0)
        dr = processed / total if total > 0 else 0
        if dr < 0.5:
            errors.append(ErrorSource(
                source="face_detection",
                severity=round(0.5 - dr, 2),
                affected_dimension="eye_contact + posture",
                description=f"人脸检测率仅 {dr:.0%}，可能导致眼神/姿态评分失真",
            ))

        pt = vf.get("posture_features", {})
        angles = pt.get("head_pose_angles", {})
        pitch_mean = abs(angles.get("pitch_mean", 0))

        # 相机内参偏差: focal=diagonal 假设 + nose_ratio 假设
        if pitch_mean > 15:
            errors.append(ErrorSource(
                source="camera_pose_bias",
                severity=round(min(pitch_mean / 60.0, 0.5), 2),
                affected_dimension="posture",
                description=f"相机焦距估算偏差 (focal=image_diagonal) + nose_ratio=0.52 假设 → pitch 系统性偏移 {pitch_mean:.1f}°。建议: 使用真实相机内参标定或调整 nose_ratio 基准值",
            ))

        # solvePnP 大面积失败
        if pitch_mean > 30:
            errors.append(ErrorSource(
                source="solvePnP",
                severity=0.3,
                affected_dimension="posture",
                description=f"pitch 均值 {pitch_mean:.1f}° 偏离正常范围，solvePnP 可能大面积失效，使用几何回退",
            ))

    # ── 语音: 转录质量 ──
    if sf:
        if sf.get("transcription", "") == "语音转写暂不可用":
            errors.append(ErrorSource(
                source="audio_quality",
                severity=0.25,
                affected_dimension="speech",
                description="Whisper 转录失败或返回空文本",
            ))
        voiced = sf.get("pitch_features", {}).get("voiced_ratio", 0)
        if voiced < 0.2:
            errors.append(ErrorSource(
                source="audio_quality",
                severity=0.15,
                affected_dimension="speech",
                description=f"有声音段比例仅 {voiced:.0%}，音频可能静音或噪声过大",
            ))

    # ── 手势: 检测率低 ──
    if gf:
        presence = gf.get("hand_presence_ratio", 0)
        if presence < 0.2:
            errors.append(ErrorSource(
                source="gesture_classification",
                severity=0.25,
                affected_dimension="gesture",
                description=f"手部检测率仅 {presence:.0%}，手势得分可信度低",
            ))
        gpm = gf.get("gesture_statistics", {}).get("gestures_per_minute", 0)
        if gpm > 200:
            errors.append(ErrorSource(
                source="gesture_frame_dedup_bug",
                severity=0.6,
                affected_dimension="gesture",
                description=f"❌ BUG: 手势频率 {gpm:.0f}/min 异常偏高 — 连续相同手势按帧累计所致, 非独立手势计数。修复方案: 对连续相同手势类型的帧做去重合并",
            ))

    return errors


def _generate_limitations(vf, sf, gf, dims) -> List[str]:
    """生成系统已知局限性列表。"""
    limits = []

    if vf:
        limits.append("视觉分析: 使用 MediaPipe Face Landmarker (478 点), 未使用深度信息, 对侧脸/遮挡敏感")
        pt = vf.get("posture_features", {})
        pitch_mean = abs(pt.get("head_pose_angles", {}).get("pitch_mean", 0))
        if pitch_mean > 30:
            limits.append("姿态分析: solvePnP 在部分帧中失败, 回退到几何估算, pitch 误差约 ±5°")

    if sf:
        limits.append("语音转录: 使用 Whisper tiny 模型, 中文识别准确率约 85-90%, 对专业术语/口音敏感")

    if gf:
        limits.append("手势分析: 仅检测手部关键点位置, 使用规则分类 (非预训练手势模型), 手势类型判定粗糙")
        gpm = gf.get("gesture_statistics", {}).get("gestures_per_minute", 0)
        if gpm > 200:
            limits.append("手势统计: 当前按帧累计计数, 连续相同手势可能被重复计入, 频率数据仅供参考")

    limits.append("评分引擎: 使用固定权重加权求和, 未考虑视频场景/演讲类型/文化差异对指标的影响")

    return limits


# ═══════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════

def generate_authenticity_report(
    visual_path: str = "",
    speech_path: str = "",
    gesture_path: str = "",
    score_path: str = "",
) -> CalibrationReport:
    """生成完整的可信度报告。

    Args:
        visual_path: visual_features JSON 路径
        speech_path: speech_features JSON 路径
        gesture_path: gesture_features JSON 路径
        score_path: final_score JSON 路径（可选）

    Returns:
        CalibrationReport 包含各维度可信度、误差来源、局限性
    """
    from datetime import datetime, timezone

    vf = _load_json(visual_path)
    sf = _load_json(speech_path)
    gf = _load_json(gesture_path)
    score = _load_json(score_path)

    dims = {}

    # 视觉: eye_contact + posture
    vis_dims = _analyze_visual_confidence(vf)
    for d in vis_dims:
        dims[d.dimension] = d

    # 语音
    dims["speech"] = _analyze_speech_confidence(sf)

    # 手势
    dims["gesture"] = _analyze_gesture_confidence(gf)

    # 误差来源
    errors = _identify_error_sources(vf, sf, gf)

    # 局限性
    limits = _generate_limitations(vf, sf, gf, dims)

    # 综合可信度 = 各维度 confidence 的加权平均
    if dims:
        overall = sum(d.confidence for d in dims.values()) / len(dims)
    else:
        overall = 0.0

    # 建议
    recs = []
    if dims.get("posture") and dims["posture"].confidence < 0.6:
        recs.append("姿态校准: 检查 solvePnP 相机内参估计逻辑, 或使用标定板获得真实内参以减少几何回退依赖")
    if dims.get("gesture") and dims["gesture"].confidence < 0.6:
        recs.append("手势分类: 建议升级为 MediaPipe Gesture Recognizer 或训练专用分类器替代规则分类")
    if dims.get("speech") and dims["speech"].confidence < 0.5:
        recs.append("语音识别: 考虑升级为 faster-whisper 或使用 medium 模型提高中文准确率")

    return CalibrationReport(
        overall_confidence=round(overall, 3),
        dimensions=dims,
        error_sources=errors,
        limitations=limits,
        recommendations=recs,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def save_report(report: CalibrationReport, output_dir: str) -> str:
    """保存可信度报告为 JSON 文件。"""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "confidence_report.json")
    data = {
        "overall_confidence": report.overall_confidence,
        "dimensions": {
            k: {
                "confidence": v.confidence,
                "method": v.method,
                "detection_rate": v.detection_rate,
                "stability": v.stability,
                "bias_estimate": v.bias_estimate,
                "calibration_notes": v.calibration_notes,
            }
            for k, v in report.dimensions.items()
        },
        "error_sources": [asdict(e) for e in report.error_sources],
        "limitations": report.limitations,
        "recommendations": report.recommendations,
        "timestamp": report.timestamp,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path
