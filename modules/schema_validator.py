"""
Schema 验证器模块
使用 jsonschema Draft7Validator 对所有模块输出 JSON 执行严格校验。
校验失败输出中文错误信息并终止流水线。
禁止在校验失败时使用 fallback 绕过，禁止 silent fallback。
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List

import jsonschema

logger = logging.getLogger("schema_validator")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_handler)

# ---------------------------------------------------------------------------
# 模块加载时读取全部 4 个 schema 文件
# ---------------------------------------------------------------------------
_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _load_schema(filename: str) -> Dict:
    """加载 JSON Schema 文件。

    Args:
        filename: schema 文件名

    Returns:
        schema 字典
    """
    path = _SCHEMA_DIR / filename
    if not path.exists():
        logger.error("Schema 文件缺失: %s，程序将退出", path)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_VISUAL_SCHEMA = _load_schema("visual_features_schema.json")
_SPEECH_SCHEMA = _load_schema("speech_features_schema.json")
_GESTURE_SCHEMA = _load_schema("gesture_features_schema.json")
_SCORE_SCHEMA = _load_schema("final_score_schema.json")


def _validate_with_schema(data: Dict, schema: Dict, label: str) -> bool:
    """使用 Draft7Validator 校验数据是否符合 schema。

    Args:
        data: 待校验的数据字典
        schema: JSON Schema 字典
        label: 校验标签（用于日志）

    Returns:
        True 通过，False 失败
    """
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        logger.error("%s 校验失败，共 %d 个错误:", label, len(errors))
        for err in errors:
            path_str = " -> ".join(str(p) for p in err.path) if err.path else "根级别"
            logger.error("  - 路径 [%s]: %s", path_str, err.message)
        return False
    return True


# ---------------------------------------------------------------------------
# 公开校验函数 — 4 个检查点 + 1 个汇总校验
# ---------------------------------------------------------------------------


def validate_visual_output(data: Dict) -> bool:
    """CHECKPOINT_1: 校验视觉分析器输出。

    Args:
        data: visual_features 字典

    Returns:
        True 通过，False 失败
    """
    if not _validate_with_schema(data, _VISUAL_SCHEMA, "视觉特征输出"):
        return False
    logger.info("CHECKPOINT_1 通过: visual_features.json 校验成功")
    return True


def validate_speech_output(data: Dict) -> bool:
    """CHECKPOINT_2: 校验语音分析器输出。

    Args:
        data: speech_features 字典

    Returns:
        True 通过，False 失败
    """
    if not _validate_with_schema(data, _SPEECH_SCHEMA, "语音特征输出"):
        return False
    logger.info("CHECKPOINT_2 通过: speech_features.json 校验成功")
    return True


def validate_gesture_output(data: Dict) -> bool:
    """CHECKPOINT_3: 校验手势分析器输出。

    Args:
        data: gesture_features 字典

    Returns:
        True 通过，False 失败
    """
    if not _validate_with_schema(data, _GESTURE_SCHEMA, "手势特征输出"):
        return False
    logger.info("CHECKPOINT_3 通过: gesture_features.json 校验成功")
    return True


def validate_score_output(data: Dict) -> bool:
    """CHECKPOINT_4: 校验评分引擎输出。

    Args:
        data: final_score 字典

    Returns:
        True 通过，False 失败
    """
    if not _validate_with_schema(data, _SCORE_SCHEMA, "评分输出"):
        return False
    logger.info("CHECKPOINT_4 通过: final_score.json 校验成功")
    return True


def validate_all_features(
    visual_path: str,
    speech_path: str,
    gesture_path: str,
) -> bool:
    """汇总校验：从磁盘重读三个特征 JSON 文件并逐个校验。

    三个特征 JSON 必须全部通过，任一失败则终止流水线。

    Args:
        visual_path: 视觉特征 JSON 文件路径
        speech_path: 语音特征 JSON 文件路径
        gesture_path: 手势特征 JSON 文件路径

    Returns:
        True 全部通过，False 任一失败
    """
    logger.info("开始汇总校验三个特征 JSON 文件...")
    all_passed = True

    # 校验视觉特征
    if os.path.isfile(visual_path):
        try:
            with open(visual_path, "r", encoding="utf-8") as f:
                vf = json.load(f)
            if not validate_visual_output(vf):
                all_passed = False
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("无法读取视觉特征文件: %s, 错误: %s", visual_path, exc)
            all_passed = False
    else:
        logger.error("视觉特征文件不存在: %s", visual_path)
        all_passed = False

    # 校验语音特征
    if os.path.isfile(speech_path):
        try:
            with open(speech_path, "r", encoding="utf-8") as f:
                sf = json.load(f)
            if not validate_speech_output(sf):
                all_passed = False
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("无法读取语音特征文件: %s, 错误: %s", speech_path, exc)
            all_passed = False
    else:
        logger.error("语音特征文件不存在: %s", speech_path)
        all_passed = False

    # 校验手势特征
    if os.path.isfile(gesture_path):
        try:
            with open(gesture_path, "r", encoding="utf-8") as f:
                gf = json.load(f)
            if not validate_gesture_output(gf):
                all_passed = False
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("无法读取手势特征文件: %s, 错误: %s", gesture_path, exc)
            all_passed = False
    else:
        logger.error("手势特征文件不存在: %s", gesture_path)
        all_passed = False

    if all_passed:
        logger.info("汇总校验通过: 三个特征 JSON 文件全部校验成功")
    else:
        logger.error("汇总校验失败: 流水线终止")

    return all_passed
