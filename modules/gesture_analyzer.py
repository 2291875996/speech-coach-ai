"""
手势分析器模块
使用 MediaPipe Tasks Hand Landmarker 检测手部关键点，基于规则分类手势类型。
仅CPU推理，离线批处理模式。
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import mediapipe as mp

logger = logging.getLogger("gesture_analyzer")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_handler)

# ---------------------------------------------------------------------------
# 模块加载时读取配置
# ---------------------------------------------------------------------------
import yaml as _yaml

_config_dir = Path(__file__).resolve().parent.parent / "config"
_yaml_path = _config_dir / "scoring_config.yaml"
_json_path = _config_dir / "thresholds_config.json"

if not _yaml_path.exists():
    logger.error("配置文件缺失: %s，程序将退出", _yaml_path)
    sys.exit(1)
if not _json_path.exists():
    logger.error("配置文件缺失: %s，程序将退出", _json_path)
    sys.exit(1)

with open(_yaml_path, "r", encoding="utf-8") as _f:
    _YAML_CFG = _yaml.safe_load(_f)
with open(_json_path, "r", encoding="utf-8") as _f:
    _JSON_CFG = json.load(_f)

_GA_CFG = _YAML_CFG.get("gesture_analyzer", {})
_GE_THRESHOLDS = _JSON_CFG.get("gesture", {})

# ---------------------------------------------------------------------------
# MediaPipe Tasks API — Hand Landmarker
# ---------------------------------------------------------------------------
_MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
_HAND_MODEL = str(_MODEL_DIR / "hand_landmarker.task")

# MediaPipe Hands 关键点索引
FINGER_TIPS = [4, 8, 12, 16, 20]     # 拇指尖、食指尖、中指尖、无名指尖、小指尖
FINGER_PIPS = [2, 6, 10, 14, 18]     # 拇指PIP (实际用IP), 食指PIP, ...
FINGER_MCPS = [1, 5, 9, 13, 17]      # 拇指MCP, 食指MCP, ...


def _create_hand_landmarker() -> Optional[object]:
    """创建 MediaPipe HandLandmarker 实例。"""
    from mediapipe.tasks.python import vision, BaseOptions

    if not os.path.isfile(_HAND_MODEL):
        logger.error("Hand Landmarker 模型文件缺失: %s", _HAND_MODEL)
        return None

    try:
        opts = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=_HAND_MODEL),
            num_hands=2,
            running_mode=vision.RunningMode.IMAGE,
            min_hand_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        return vision.HandLandmarker.create_from_options(opts)
    except Exception as exc:
        logger.error("创建 HandLandmarker 失败: %s", exc)
        return None


def _classify_finger_extended(
    landmarks: np.ndarray, finger_idx: int, is_thumb: bool = False
) -> bool:
    """判断单根手指是否伸展。

    非拇指：比较指尖 y 坐标 < PIP y 坐标（手指向上时 y 较小）
    拇指：比较指尖与 MCP 的欧氏距离

    Args:
        landmarks: 21 个关键点的归一化坐标 (21, 2)，原点在图像左上角
        finger_idx: 0=拇指, 1=食指, 2=中指, 3=无名指, 4=小指
        is_thumb: 是否为拇指

    Returns:
        True 表示手指伸展
    """
    tip_idx = FINGER_TIPS[finger_idx]
    pip_idx = FINGER_PIPS[finger_idx]
    mcp_idx = FINGER_MCPS[finger_idx]

    tip = landmarks[tip_idx]
    pip = landmarks[pip_idx]
    mcp = landmarks[mcp_idx]

    if is_thumb:
        dist = np.linalg.norm(tip - mcp)
        return dist > 0.06
    else:
        return float(tip[1]) < float(pip[1]) - 0.01


def _classify_gesture(hand_landmarks: np.ndarray, handedness: str) -> str:
    """基于手部关键点分类手势类型。

    Args:
        hand_landmarks: (21, 2) 归一化坐标
        handedness: "Left" 或 "Right"

    Returns:
        手势类别: "open_palm", "closed_fist", "pointing", "other"
    """
    fingers_extended = []
    for i in range(5):
        is_thumb = (i == 0)
        fingers_extended.append(
            _classify_finger_extended(hand_landmarks, i, is_thumb)
        )

    extended_count = sum(fingers_extended)

    if extended_count >= 4:
        return "open_palm"
    if extended_count <= 1:
        return "closed_fist"
    if fingers_extended[1] and not fingers_extended[2] and not fingers_extended[3]:
        return "pointing"
    return "other"


def _compute_hand_velocity(
    current_pos: Optional[np.ndarray],
    previous_pos: Optional[np.ndarray],
    fps: float,
) -> float:
    """计算手部运动速度（归一化坐标/秒）。"""
    if current_pos is None or previous_pos is None or fps <= 0:
        return 0.0
    displacement = np.linalg.norm(current_pos - previous_pos)
    return float(displacement * fps)


def _build_default_output(source: str) -> Dict:
    """构建空手势特征的默认回退输出。"""
    return {
        "source": source,
        "frame_count_total": 0,
        "frame_count_processed": 0,
        "hand_presence_ratio": 0.0,
        "duration_seconds": 0.0,
        "gesture_statistics": {
            "gestures_per_minute": 0.0,
            "gesture_variety_count": 0,
            "total_gestures_detected": 0,
            "dominant_hand": "unknown",
        },
        "gesture_classification": {
            "open_palm": {"count": 0, "ratio": 0.0},
            "closed_fist": {"count": 0, "ratio": 0.0},
            "pointing": {"count": 0, "ratio": 0.0},
            "other": {"count": 0, "ratio": 0.0},
        },
        "hand_movement": {
            "mean_velocity": 0.0,
            "max_velocity": 0.0,
            "movement_range_x": 0.0,
            "movement_range_y": 0.0,
            "movement_smoothness": 0.0,
        },
        "dimension_scores": {"gesture_score": 0.0},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def analyze(video_path: str, output_dir: Optional[str] = None) -> Dict:
    """分析视频中的手势特征。

    Args:
        video_path: 输入视频文件路径
        output_dir: 特征输出目录

    Returns:
        手势特征字典
    """
    logger.info("开始手势分析: %s", video_path)

    if not os.path.isfile(video_path):
        logger.warning("视频文件不存在: %s，返回默认输出", video_path)
        output = _build_default_output(video_path)
        _save_output(output, output_dir)
        return output

    cap = None
    hand_landmarker = None

    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"无法打开视频文件: {video_path}")

        frame_count_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0
        duration = frame_count_total / fps if frame_count_total > 0 else 0.0

        sample_interval = _GA_CFG.get("frame_sample_interval", 3)

        # 创建 MediaPipe HandLandmarker（新 Tasks API）
        hand_landmarker = _create_hand_landmarker()
        if hand_landmarker is None:
            logger.warning("无法创建 HandLandmarker，返回默认值")
            output = _build_default_output(video_path)
            _save_output(output, output_dir)
            return output

        # 累积数据
        gesture_counts = {"open_palm": 0, "closed_fist": 0, "pointing": 0, "other": 0}
        gesture_sequence: List[str] = []
        hand_presence_frames = 0
        processed = 0
        frame_idx = 0
        last_gesture = None

        # 运动追踪
        velocities: List[float] = []
        wrist_positions: List[Tuple[float, float]] = []
        prev_wrist_pos: Optional[np.ndarray] = None

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % sample_interval != 0:
                frame_idx += 1
                continue

            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # 使用新 Tasks API
            mp_img = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=frame_rgb,
            )
            result = hand_landmarker.detect(mp_img)

            if result.hand_landmarks is None or len(result.hand_landmarks) == 0:
                frame_idx += 1
                continue

            processed += 1
            hand_presence_frames += 1

            # 循环处理检测到的每只手
            for idx, hand_lms in enumerate(result.hand_landmarks):
                h, w = frame.shape[:2]
                pts = np.array(
                    [[lm.x, lm.y] for lm in hand_lms],
                    dtype=np.float64,
                )

                # 获取左右手信息
                handedness = "Right"
                if result.handedness and idx < len(result.handedness):
                    cat = result.handedness[idx][0]
                    handedness = cat.category_name if hasattr(cat, 'category_name') else "Right"

                gesture = _classify_gesture(pts, handedness)
                gesture_counts[gesture] += 1

                # 手势变更检测
                if gesture != last_gesture and last_gesture is not None:
                    gesture_sequence.append(gesture)
                last_gesture = gesture

                # 腕部运动 (landmark 0)
                wrist_pos = pts[0]
                wrist_positions.append((float(wrist_pos[0]), float(wrist_pos[1])))
                vel = _compute_hand_velocity(wrist_pos, prev_wrist_pos, fps)
                velocities.append(vel)
                prev_wrist_pos = wrist_pos.copy()

                # 每帧最多处理 2 只手（API 已限制 num_hands=2）
                if idx >= 1:
                    break

            frame_idx += 1

        cap.release()
        hand_landmarker.close()

        # 手部最小出现比例检查
        min_ratio = _GA_CFG.get("min_hand_presence_ratio", 0.1)
        hand_presence = hand_presence_frames / processed if processed > 0 else 0.0

        if processed == 0 or hand_presence < min_ratio:
            logger.warning("手部出现比例过低 (%.2f)，返回空手势特征", hand_presence)
            output = _build_default_output(video_path)
            output["frame_count_total"] = frame_count_total
            output["duration_seconds"] = duration
            _save_output(output, output_dir)
            return output

        # --- 聚合统计 ---
        total_gestures = sum(gesture_counts.values())
        meaningful_gestures = total_gestures - gesture_counts.get("other", 0)
        gestures_per_min = (meaningful_gestures / duration * 60.0) if duration > 0 else 0.0

        variety_set = set()
        for g in ["open_palm", "closed_fist", "pointing"]:
            if gesture_counts.get(g, 0) > 0:
                variety_set.add(g)
        variety_count = len(variety_set)

        vel_arr = np.array(velocities) if velocities else np.array([0.0])
        if wrist_positions:
            xs = [p[0] for p in wrist_positions]
            ys = [p[1] for p in wrist_positions]
            range_x = max(xs) - min(xs)
            range_y = max(ys) - min(ys)
        else:
            range_x = 0.0
            range_y = 0.0

        mean_vel = float(vel_arr.mean())
        std_vel = float(vel_arr.std())
        smoothness = 1.0 - min(1.0, std_vel / (mean_vel + 1e-6))

        # --- 维度评分 ---
        ge = _GE_THRESHOLDS
        min_exc = ge.get("min_gestures_per_minute_excellent", 5.0)
        min_good = ge.get("min_gestures_per_minute_good", 2.0)
        if gestures_per_min >= min_exc:
            freq_score = 100.0
        elif gestures_per_min >= min_good:
            freq_score = 70.0 + 30.0 * (gestures_per_min - min_good) / (min_exc - min_good)
        elif gestures_per_min > 0:
            freq_score = max(0.0, 70.0 * (gestures_per_min / min_good))
        else:
            freq_score = 0.0

        variety_exc = ge.get("min_gesture_variety_excellent", 4)
        variety_good = ge.get("min_gesture_variety_good", 2)
        if variety_count >= variety_exc:
            variety_score = 100.0
        elif variety_count >= variety_good:
            variety_score = 70.0 + 30.0 * (variety_count - variety_good) / (variety_exc - variety_good)
        else:
            variety_score = max(0.0, 70.0 * (variety_count / max(variety_good, 1)))

        ideal_vel = 0.05
        movement_score = min(100.0, (mean_vel / ideal_vel) * 100.0)
        movement_score = max(0.0, 100.0 - abs(100.0 - movement_score))

        freq_w = _GA_CFG.get("frequency_weight", 0.40)
        variety_w = _GA_CFG.get("variety_weight", 0.35)
        movement_w = _GA_CFG.get("movement_weight", 0.25)
        gesture_score = freq_w * freq_score + variety_w * variety_score + movement_w * movement_score
        gesture_score = max(0.0, min(100.0, gesture_score))

        if total_gestures > 0:
            open_palm_ratio = gesture_counts["open_palm"] / total_gestures
            closed_fist_ratio = gesture_counts["closed_fist"] / total_gestures
            pointing_ratio = gesture_counts["pointing"] / total_gestures
            other_ratio = gesture_counts["other"] / total_gestures
        else:
            open_palm_ratio = closed_fist_ratio = pointing_ratio = other_ratio = 0.0

        # 注: 当前实现仅区分 "both" 和 "right"，"left" 在 Schema enum 中为未来预留
        dominant = "both" if hand_presence > 0.7 else "right"

        output = {
            "source": video_path,
            "frame_count_total": frame_count_total,
            "frame_count_processed": processed,
            "hand_presence_ratio": float(hand_presence),
            "duration_seconds": duration,
            "gesture_statistics": {
                "gestures_per_minute": float(gestures_per_min),
                "gesture_variety_count": variety_count,
                "total_gestures_detected": int(meaningful_gestures),
                "dominant_hand": dominant,
            },
            "gesture_classification": {
                "open_palm": {"count": gesture_counts["open_palm"], "ratio": float(open_palm_ratio)},
                "closed_fist": {"count": gesture_counts["closed_fist"], "ratio": float(closed_fist_ratio)},
                "pointing": {"count": gesture_counts["pointing"], "ratio": float(pointing_ratio)},
                "other": {"count": gesture_counts["other"], "ratio": float(other_ratio)},
            },
            "hand_movement": {
                "mean_velocity": float(mean_vel),
                "max_velocity": float(vel_arr.max()),
                "movement_range_x": float(range_x),
                "movement_range_y": float(range_y),
                "movement_smoothness": float(smoothness),
            },
            "dimension_scores": {"gesture_score": float(gesture_score)},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        logger.info("手势分析完成: 手势/分钟=%.1f, 种类=%d, 得分=%.1f",
                     gestures_per_min, variety_count, gesture_score)
        _save_output(output, output_dir)
        return output

    except Exception as exc:
        logger.error("手势分析异常: %s，返回默认值", exc)
        output = _build_default_output(video_path)
        _save_output(output, output_dir)
        return output
    finally:
        if cap is not None and cap.isOpened():
            cap.release()
        if hand_landmarker is not None:
            try:
                hand_landmarker.close()
            except Exception:
                pass


def _save_output(features: Dict, output_dir: Optional[str] = None) -> str:
    """将特征字典写入 JSON 文件。"""
    if output_dir is None:
        output_dir = str(
            Path(__file__).resolve().parent.parent / "output" / "features"
        )
    os.makedirs(output_dir, exist_ok=True)

    source_name = Path(features["source"]).stem
    filename = f"gesture_features_{source_name}.json"
    output_path = os.path.join(output_dir, filename)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(features, f, ensure_ascii=False, indent=2)

    logger.info("手势特征已写入: %s", output_path)
    return output_path
