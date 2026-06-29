"""
评分引擎模块
从 YAML 配置文件读取权重，从 JSON 配置文件读取阈值，
基于规则计算四维度分和综合得分。
禁止在代码中硬编码评分权重数字。
"""

import json
import logging
import os
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("scoring_engine")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_handler)


def _load_scoring_weights() -> Dict[str, float]:
    """从 scoring_config.yaml 加载评分权重。

    程序启动时调用，配置文件缺失时报错退出并输出中文错误日志。

    Returns:
        权重字典 {'eye_contact': ..., 'posture': ..., 'gesture': ..., 'speech': ...}
    """
    config_dir = Path(__file__).resolve().parent.parent / "config"
    yaml_path = config_dir / "scoring_config.yaml"

    if not yaml_path.exists():
        logger.error("配置文件缺失: %s，程序将退出", yaml_path)
        sys.exit(1)

    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    weights = config["weights"]
    required = ["eye_contact", "posture", "gesture", "speech"]
    for key in required:
        if key not in weights:
            logger.error("评分权重配置缺失: '%s'，程序将退出", key)
            sys.exit(1)

    return {
        "eye_contact": float(weights["eye_contact"]),
        "posture": float(weights["posture"]),
        "gesture": float(weights["gesture"]),
        "speech": float(weights["speech"]),
    }


def _load_grade_thresholds() -> Dict[str, float]:
    """从 thresholds_config.json 加载评分等级阈值。"""
    config_dir = Path(__file__).resolve().parent.parent / "config"
    json_path = config_dir / "thresholds_config.json"

    if not json_path.exists():
        logger.error("阈值配置文件缺失: %s，程序将退出", json_path)
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        thresholds_config = json.load(f)

    grade_thresholds = thresholds_config.get("grade_thresholds", {
        "excellent": 85,
        "good": 70,
        "average": 50,
    })
    return grade_thresholds


def _load_fallback_score() -> float:
    """从配置文件加载缺失维度回退分数。"""
    config_dir = Path(__file__).resolve().parent.parent / "config"
    yaml_path = config_dir / "scoring_config.yaml"

    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    se_cfg = config.get("scoring_engine", {})
    return float(se_cfg.get("missing_dimension_fallback", 50.0))


def _determine_grade(score: float) -> str:
    """根据综合得分判定评分等级。

    评分等级（从配置文件读取阈值）：
        85-100 → 优秀
        70-84  → 良好
        50-69  → 一般
        0-49   → 需改进
    """
    thresholds = _load_grade_thresholds()
    if score >= thresholds.get("excellent", 85):
        return "优秀"
    if score >= thresholds.get("good", 70):
        return "良好"
    if score >= thresholds.get("average", 50):
        return "一般"
    return "需改进"


def compute(
    visual_features_path: str,
    speech_features_path: str,
    gesture_features_path: str,
    output_dir: Optional[str] = None,
) -> Dict:
    """读取三个特征 JSON 文件，计算综合评分并输出最终评分 JSON。

    Args:
        visual_features_path: 视觉特征 JSON 文件路径
        speech_features_path: 语音特征 JSON 文件路径
        gesture_features_path: 手势特征 JSON 文件路径
        output_dir: 评分输出目录，默认 output/reports/

    Returns:
        最终评分字典
    """
    logger.info("开始综合评分计算")

    # --- 加载配置（从配置文件，禁止硬编码） ---
    # 第2行 读取scoring_config.yaml → weights
    weights = _load_scoring_weights()
    # 第3行 读取thresholds_config.json → 验证阈值可用
    _load_grade_thresholds()
    fallback_score = _load_fallback_score()
    warnings: List[str] = []

    # 第4行 从配置取权重 → 独立变量
    eye_contact_weight = weights["eye_contact"]
    posture_weight = weights["posture"]
    gesture_weight = weights["gesture"]
    speech_weight = weights["speech"]

    logger.info(
        "已加载评分权重: 眼神=%.2f, 姿态=%.2f, 手势=%.2f, 语音=%.2f",
        eye_contact_weight,
        posture_weight,
        gesture_weight,
        speech_weight,
    )
    logger.info("回退分数（缺失维度使用）: %.1f", fallback_score)

    # --- 读取并提取各维度得分 ---
    dimension_scores: Dict[str, float] = {}
    source_files = {
        "visual_features": visual_features_path,
        "speech_features": speech_features_path,
        "gesture_features": gesture_features_path,
    }

    # 视觉特征 → 眼神交流得分 + 姿态得分
    eye_contact_score = fallback_score
    posture_score = fallback_score

    if os.path.isfile(visual_features_path):
        try:
            with open(visual_features_path, "r", encoding="utf-8") as f:
                vf = json.load(f)
            dims = vf.get("dimension_scores", {})
            ec = dims.get("eye_contact_score")
            ps = dims.get("posture_score")
            if ec is not None:
                eye_contact_score = float(ec)
            else:
                warnings.append("视觉特征中缺少眼神交流得分，使用回退分数 %.0f" % fallback_score)
                logger.warning("视觉特征中缺少眼神交流得分，使用回退分数 %.0f", fallback_score)
            if ps is not None:
                posture_score = float(ps)
            else:
                warnings.append("视觉特征中缺少姿态得分，使用回退分数 %.0f" % fallback_score)
                logger.warning("视觉特征中缺少姿态得分，使用回退分数 %.0f", fallback_score)
        except (json.JSONDecodeError, KeyError) as exc:
            warnings.append("视觉特征文件解析失败: %s，使用回退分数 %.0f" % (exc, fallback_score))
            logger.warning("视觉特征文件解析失败: %s，使用回退分数 %.0f", exc, fallback_score)
    else:
        warnings.append("视觉特征文件不存在: %s，使用回退分数 %.0f" % (visual_features_path, fallback_score))
        logger.warning("视觉特征文件不存在: %s，使用回退分数 %.0f", visual_features_path, fallback_score)

    dimension_scores["eye_contact_score"] = eye_contact_score
    dimension_scores["posture_score"] = posture_score

    logger.info("【诊断】视觉特征 → 眼神交流=%.1f, 姿态=%.1f (回退值=%.0f)",
                eye_contact_score, posture_score, fallback_score)

    # 语音特征 → 语音表达得分
    speech_score = fallback_score
    if os.path.isfile(speech_features_path):
        try:
            with open(speech_features_path, "r", encoding="utf-8") as f:
                sf = json.load(f)
            ss = sf.get("dimension_scores", {}).get("speech_score")
            if ss is not None:
                speech_score = float(ss)
            else:
                warnings.append("语音特征中缺少语音得分，使用回退分数 %.0f" % fallback_score)
                logger.warning("语音特征中缺少语音得分，使用回退分数 %.0f", fallback_score)
        except (json.JSONDecodeError, KeyError) as exc:
            warnings.append("语音特征文件解析失败: %s，使用回退分数 %.0f" % (exc, fallback_score))
            logger.warning("语音特征文件解析失败: %s，使用回退分数 %.0f", exc, fallback_score)
    else:
        warnings.append("语音特征文件不存在: %s，使用回退分数 %.0f" % (speech_features_path, fallback_score))
        logger.warning("语音特征文件不存在: %s，使用回退分数 %.0f", speech_features_path, fallback_score)

    dimension_scores["speech_score"] = speech_score

    logger.info("【诊断】语音特征 → 语音表达=%.1f (回退值=%.0f)",
                speech_score, fallback_score)

    # 手势特征 → 手势得分
    gesture_score = fallback_score
    if os.path.isfile(gesture_features_path):
        try:
            with open(gesture_features_path, "r", encoding="utf-8") as f:
                gf = json.load(f)
            gs = gf.get("dimension_scores", {}).get("gesture_score")
            if gs is not None:
                gesture_score = float(gs)
            else:
                warnings.append("手势特征中缺少手势得分，使用回退分数 %.0f" % fallback_score)
                logger.warning("手势特征中缺少手势得分，使用回退分数 %.0f", fallback_score)
        except (json.JSONDecodeError, KeyError) as exc:
            warnings.append("手势特征文件解析失败: %s，使用回退分数 %.0f" % (exc, fallback_score))
            logger.warning("手势特征文件解析失败: %s，使用回退分数 %.0f", exc, fallback_score)
    else:
        warnings.append("手势特征文件不存在: %s，使用回退分数 %.0f" % (gesture_features_path, fallback_score))
        logger.warning("手势特征文件不存在: %s，使用回退分数 %.0f", gesture_features_path, fallback_score)

    dimension_scores["gesture_score"] = gesture_score

    logger.info("【诊断】手势特征 → 手势=%.1f (回退值=%.0f)",
                gesture_score, fallback_score)

    # --- 计算综合得分 ---
    # 公式：综合得分 = 眼神交流×w1 + 姿态×w2 + 手势×w3 + 语音×w4

    logger.info("=" * 50)
    logger.info("【诊断】综合评分计算明细:")
    logger.info("  眼神交流: %.1f × %.2f = %.2f", eye_contact_score, eye_contact_weight, eye_contact_weight * eye_contact_score)
    logger.info("  姿态表现: %.1f × %.2f = %.2f", posture_score, posture_weight, posture_weight * posture_score)
    logger.info("  手势表达: %.1f × %.2f = %.2f", gesture_score, gesture_weight, gesture_weight * gesture_score)
    logger.info("  语音表达: %.1f × %.2f = %.2f", speech_score, speech_weight, speech_weight * speech_score)

    overall_score = (
        eye_contact_weight * eye_contact_score
        + posture_weight * posture_score
        + gesture_weight * gesture_score
        + speech_weight * speech_score
    )

    logger.info("  综合得分(限幅前): %.2f", overall_score)

    # 限幅到 [0, 100]
    overall_score = max(0.0, min(100.0, overall_score))

    # 评定等级
    grade = _determine_grade(overall_score)

    # --- 构建输出 ---
    result = {
        "overall_score": float(round(overall_score, 2)),
        "grade": grade,
        "dimension_scores": {
            "eye_contact_score": float(round(eye_contact_score, 2)),
            "posture_score": float(round(posture_score, 2)),
            "gesture_score": float(round(gesture_score, 2)),
            "speech_score": float(round(speech_score, 2)),
        },
        "weights_used": {
            "eye_contact": eye_contact_weight,
            "posture": posture_weight,
            "gesture": gesture_weight,
            "speech": speech_weight,
        },
        "source_files": source_files,
        "warnings": warnings,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info("评分计算完成: %.1f 分, 等级: %s", overall_score, grade)
    logger.info("【诊断】各维度最终得分: 眼神=%.1f / 姿态=%.1f / 手势=%.1f / 语音=%.1f",
                eye_contact_score, posture_score, gesture_score, speech_score)
    logger.info("=" * 50)

    # --- 写入输出 ---
    _save_output(result, output_dir)
    return result


def _save_output(score_data: Dict, output_dir: Optional[str] = None) -> str:
    """将最终评分写入 JSON 文件。"""
    if output_dir is None:
        output_dir = str(
            Path(__file__).resolve().parent.parent / "output" / "reports"
        )
    os.makedirs(output_dir, exist_ok=True)

    # 使用时间戳生成唯一文件名
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"final_score_{ts}.json"
    output_path = os.path.join(output_dir, filename)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(score_data, f, ensure_ascii=False, indent=2)

    logger.info("最终评分已写入: %s", output_path)
    return output_path
