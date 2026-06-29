"""
视觉分析器模块
使用 MediaPipe Tasks Face Landmarker 分析视频中的面部特征，包括眼神交流和头部姿态。
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

# ---------------------------------------------------------------------------
# 日志配置：中文日志信息
# ---------------------------------------------------------------------------
logger = logging.getLogger("visual_analyzer")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_handler)

# ---------------------------------------------------------------------------
# MediaPipe Tasks API — Face Landmarker
# ---------------------------------------------------------------------------
_MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
_FACE_MODEL = str(_MODEL_DIR / "face_landmarker.task")

# MediaPipe Face Mesh 关键点索引常量（478 点模型，与旧版 468 点兼容子集）
# 左眼 EAR 六点: p1(左角) p2(上) p3(右角) p4(下) p5 p6
LEFT_EYE_IDX = [33, 160, 158, 133, 153, 144]
# 右眼 EAR 六点
RIGHT_EYE_IDX = [362, 385, 387, 263, 373, 380]

# solvePnP 使用的 2D 关键点索引
HEAD_POSE_LANDMARKS = {
    "nose_tip": 1,
    "chin": 152,
    "left_eye_corner": 33,
    "right_eye_corner": 263,
    "left_mouth_corner": 61,
    "right_mouth_corner": 291,
}

# 通用 3D 人脸模型坐标 (mm)，用于 solvePnP
GENERIC_FACE_3D = np.array(
    [
        [0.0, 0.0, 0.0],        # nose tip
        [0.0, -63.6, -12.5],     # chin
        [-33.0, 30.0, -15.0],    # left eye left corner
        [33.0, 30.0, -15.0],     # right eye right corner
        [-27.0, -15.0, -10.0],   # left mouth corner
        [27.0, -15.0, -10.0],    # right mouth corner
    ],
    dtype=np.float64,
)


def _load_config() -> Tuple[Dict, Dict]:
    """从配置文件加载视觉分析器参数和阈值。"""
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

    return yaml_cfg, json_cfg


# 模块加载时读取配置
_YAML_CFG, _JSON_CFG = _load_config()
_VA_CFG = _YAML_CFG.get("visual_analyzer", {})
_EC_THRESHOLDS = _JSON_CFG.get("eye_contact", {})
_PT_THRESHOLDS = _JSON_CFG.get("posture", {})


def _crop_letterbox(frame: np.ndarray, black_threshold: int = 15,
                    max_dim: int = 640) -> np.ndarray:
    """裁剪画面中的黑边（letterbox / pillarbox）并缩放到合理尺寸。

    MediaPipe FaceLandmarker 在输入尺寸过大时可能漏检小脸。
    裁剪黑边后限制最大边长为 max_dim，确保人脸占画面足够比例。

    Args:
        frame: BGR 图像 (H, W, 3)
        black_threshold: 判定为"黑边"的像素值上限
        max_dim: 输出图像最大边长（像素），0 表示不缩放

    Returns:
        处理后的图像
    """
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # 二值化：非黑像素为 255
    _, thresh = cv2.threshold(gray, black_threshold, 255, cv2.THRESH_BINARY)
    # 找非黑像素的包围盒
    coords = cv2.findNonZero(thresh)
    if coords is not None:
        x, y, bw, bh = cv2.boundingRect(coords)
        margin_pct = 0.05
        if (x > w * margin_pct or y > h * margin_pct
                or (w - x - bw) > w * margin_pct
                or (h - y - bh) > h * margin_pct):
            frame = frame[y:y + bh, x:x + bw]
    # 缩放：限制最大边长
    if max_dim > 0:
        h2, w2 = frame.shape[:2]
        max_side = max(h2, w2)
        if max_side > max_dim:
            scale = max_dim / max_side
            new_w, new_h = int(w2 * scale), int(h2 * scale)
            frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return frame


def _create_face_landmarker() -> Optional[object]:
    """创建 MediaPipe FaceLandmarker 实例。"""
    from mediapipe.tasks.python import vision, BaseOptions

    if not os.path.isfile(_FACE_MODEL):
        logger.error("Face Landmarker 模型文件缺失: %s", _FACE_MODEL)
        return None

    try:
        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=_FACE_MODEL),
            num_faces=1,
            running_mode=vision.RunningMode.IMAGE,
            min_face_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        return vision.FaceLandmarker.create_from_options(opts)
    except Exception as exc:
        logger.error("创建 FaceLandmarker 失败: %s", exc)
        return None


def _compute_ear(landmarks: np.ndarray, eye_indices: List[int]) -> float:
    """计算单只眼睛的 Eye Aspect Ratio (EAR)。

    EAR = (||p2-p6|| + ||p3-p5||) / (2 * ||p1-p4||)

    Args:
        landmarks: 归一化的面部关键点坐标数组 (N, 2)，单位像素
        eye_indices: 六点索引列表 [p1(l),p2(t),p3(r),p4(b),p5,p6]

    Returns:
        EAR 值
    """
    p = landmarks[eye_indices]
    vertical_1 = np.linalg.norm(p[1] - p[5])
    vertical_2 = np.linalg.norm(p[2] - p[4])
    horizontal = np.linalg.norm(p[0] - p[3])
    if horizontal < 1e-6:
        return 0.0
    ear = (vertical_1 + vertical_2) / (2.0 * horizontal)
    return float(ear)


def _rotation_vector_to_euler(rvec: np.ndarray) -> Tuple[float, float, float]:
    """将旋转向量转换为欧拉角（pitch, yaw, roll），单位度。

    Args:
        rvec: 旋转向量 (3,1)

    Returns:
        (pitch, yaw, roll) 单位度
    """
    rmat, _ = cv2.Rodrigues(rvec)
    sy = np.sqrt(rmat[0, 0] ** 2 + rmat[1, 0] ** 2)
    singular = sy < 1e-6
    if not singular:
        pitch = np.arctan2(rmat[2, 1], rmat[2, 2])
        yaw = np.arctan2(-rmat[2, 0], sy)
        roll = np.arctan2(rmat[1, 0], rmat[0, 0])
    else:
        pitch = np.arctan2(-rmat[1, 2], rmat[1, 1])
        yaw = np.arctan2(-rmat[2, 0], sy)
        roll = 0.0
    return (
        float(np.degrees(pitch)),
        float(np.degrees(yaw)),
        float(np.degrees(roll)),
    )


def _estimate_head_pose(
    face_landmarks: np.ndarray, img_w: int, img_h: int, camera_matrix: np.ndarray
) -> Tuple[float, float, float]:
    """估算头部姿态（pitch, yaw, roll），单位度。

    优先使用 solvePnP；若结果异常（|pitch|>80° 或 |roll|>80°），
    回退到基于 landmark 几何关系的稳健估算。

    Args:
        face_landmarks: N 个面部关键点 (N, 2) 像素坐标
        img_w: 图像宽度
        img_h: 图像高度
        camera_matrix: 相机内参矩阵 (3,3)

    Returns:
        (pitch, yaw, roll, from_solvePnP) — from_solvePnP=True 表示 solvePnP 成功
    """
    # 提取关键点
    nose = face_landmarks[HEAD_POSE_LANDMARKS["nose_tip"]]
    chin = face_landmarks[HEAD_POSE_LANDMARKS["chin"]]
    left_eye = face_landmarks[HEAD_POSE_LANDMARKS["left_eye_corner"]]
    right_eye = face_landmarks[HEAD_POSE_LANDMARKS["right_eye_corner"]]
    left_mouth = face_landmarks[HEAD_POSE_LANDMARKS["left_mouth_corner"]]
    right_mouth = face_landmarks[HEAD_POSE_LANDMARKS["right_mouth_corner"]]

    # 先尝试 solvePnP
    img_pts = np.array([nose, chin, left_eye, right_eye, left_mouth, right_mouth],
                       dtype=np.float64)
    dist_coeffs = np.zeros((4, 1), dtype=np.float64)
    success, rvec, tvec = cv2.solvePnP(
        GENERIC_FACE_3D, img_pts, camera_matrix, dist_coeffs,
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if success:
        pitch, yaw, roll = _rotation_vector_to_euler(rvec)
        # 若角度在合理范围（±80°），直接采用
        if abs(pitch) < 80.0 and abs(roll) < 80.0:
            return (pitch, yaw, roll, True)  # solvePnP 成功

    # --- slovePnP 异常时的稳健回退 ---
    # 计算面部几何中心
    eye_center = (left_eye + right_eye) / 2.0
    mouth_center = (left_mouth + right_mouth) / 2.0

    # Pitch: 基于眼-鼻-嘴垂直比例
    # 参照值 0.52（适配摄像头低于眼线位的常见场景），偏离范围 0.20
    # 纯正面平视时 nose_ratio ≈ 0.48-0.56（因人而异），本参照值取中
    eye_to_mouth = mouth_center[1] - eye_center[1]
    if eye_to_mouth > 1:
        nose_ratio = (nose[1] - eye_center[1]) / eye_to_mouth
        pitch_norm = (nose_ratio - 0.52) / 0.20
        pitch = np.clip(pitch_norm * 45.0, -60.0, 60.0)
    else:
        pitch = 0.0

    # Yaw: 基于鼻尖相对眼中心的水平偏移
    eye_width = right_eye[0] - left_eye[0]
    if eye_width > 1:
        nose_offset = (nose[0] - eye_center[0]) / eye_width
        yaw = np.clip(nose_offset * 60.0, -70.0, 70.0)
    else:
        yaw = 0.0

    # Roll: 基于双眼连线角度
    eye_dx = right_eye[0] - left_eye[0]
    eye_dy = right_eye[1] - left_eye[1]
    roll = np.degrees(np.arctan2(eye_dy, eye_dx)) if abs(eye_dx) > 0.1 else 0.0

    return (pitch, yaw, roll, False)  # 几何回退


def _build_default_output(source: str) -> Dict:
    """构建默认的全零回退输出结构。"""
    return {
        "source": source,
        "frame_count_total": 0,
        "frame_count_processed": 0,
        "fps": 0.0,
        "duration_seconds": 0.0,
        "eye_contact_features": {
            "ear_left_mean": 0.0,
            "ear_right_mean": 0.0,
            "ear_left_std": 0.0,
            "ear_right_std": 0.0,
            "contact_ratio": 0.0,
            "blink_count": 0,
            "blink_rate_per_minute": 0.0,
        },
        "posture_features": {
            "head_pose_angles": {
                "pitch_mean": 0.0,
                "pitch_std": 0.0,
                "yaw_mean": 0.0,
                "yaw_std": 0.0,
                "roll_mean": 0.0,
                "roll_std": 0.0,
            },
            "posture_stability": 0.0,
            "posture_uprightness": 0.0,
        },
        "dimension_scores": {
            "eye_contact_score": 0.0,
            "posture_score": 0.0,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def analyze(video_path: str, output_dir: Optional[str] = None) -> Dict:
    """分析视频中的视觉特征（眼神交流 + 头部姿态）。

    Args:
        video_path: 输入视频文件路径
        output_dir: 特征输出目录，若为 None 则默认写入 output/features/

    Returns:
        视觉特征字典
    """
    logger.info("开始视觉分析: %s", video_path)

    # --- 验证输入 ---
    if not os.path.isfile(video_path):
        logger.error("视频文件不存在: %s", video_path)
        output = _build_default_output(video_path)
        _save_output(output, output_dir)
        return output

    # --- 初始化 ---
    cap = None
    face_landmarker = None

    try:
        # 打开视频
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"无法打开视频文件: {video_path}")

        frame_count_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0
        duration = frame_count_total / fps if frame_count_total > 0 else 0.0
        img_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        img_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # 创建 MediaPipe FaceLandmarker（新 Tasks API）
        face_landmarker = _create_face_landmarker()
        if face_landmarker is None:
            logger.error("【诊断】FaceLandmarker 创建失败 — 模型文件或 API 不可用")
            output = _build_default_output(video_path)
            _save_output(output, output_dir)
            return output
        logger.info("【诊断】FaceLandmarker 创建成功, 模型: %s", _FACE_MODEL)

        # 采样参数 — 从配置读取
        sample_interval = _VA_CFG.get("frame_sample_interval", 5)
        if sample_interval > 3:
            sample_interval = 3
        ear_blink_threshold = _VA_CFG.get("ear_threshold_blink", 0.2)
        blink_consecutive_frames = _VA_CFG.get("ear_consecutive_blink_frames", 3)
        eye_contact_angle = _VA_CFG.get("eye_contact_angle_threshold_deg", 25.0)

        logger.info("【诊断】采样间隔=%d (全帧检测), 视频=%dx%d, %.1ffps, %.0f帧",
                     sample_interval, img_w, img_h, fps, float(frame_count_total))

        # 累积数据容器
        ear_left_list: List[float] = []
        ear_right_list: List[float] = []
        pitch_list: List[float] = []
        yaw_list: List[float] = []
        roll_list: List[float] = []

        blink_count = 0
        low_ear_counter = 0
        contact_frames = 0

        # 诊断计数器（分离计数）
        total_frames_read = 0       # cap.read() 返回 True 的帧数
        processed_frames = 0        # 送入 FaceLandmarker 的帧数
        detected_face_frames = 0    # 检测到人脸的帧数
        solvepnp_ok = 0             # solvePnP 直接成功的次数
        solvepnp_fallback = 0       # 回退到几何估算的次数
        frame_idx = 0
        debug_saved = False

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            total_frames_read += 1

            if frame_idx % sample_interval != 0:
                frame_idx += 1
                continue

            processed_frames += 1

            # 裁剪 letterbox 黑边 + 缩放到 640px（确保人脸尺寸足够）
            frame = _crop_letterbox(frame)
            fh, fw = frame.shape[:2]
            # 基于当前帧尺寸计算相机内参（focal_length 取对角线长度，避免过短焦距导致 solvePnP 异常）
            focal = float(np.sqrt(fw * fw + fh * fh))
            camera_matrix = np.array(
                [[focal, 0.0, fw / 2.0],
                 [0.0, focal, fh / 2.0],
                 [0.0, 0.0, 1.0]],
                dtype=np.float64,
            )

            # 保存第一帧用于人工检查
            if not debug_saved:
                debug_path = os.path.join(
                    output_dir or str(Path(__file__).resolve().parent.parent / "output"),
                    "debug_frame.jpg",
                )
                cv2.imwrite(debug_path, frame)
                logger.info("【诊断】第一帧已保存: %s (尺寸 %dx%d)",
                            debug_path, frame.shape[1], frame.shape[0])
                debug_saved = True

            # BGR → RGB 转换
            try:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            except Exception as exc:
                logger.error("【诊断】cvtColor 失败 (帧#%d): %s", frame_idx, exc)
                frame_idx += 1
                continue

            # 使用新 Tasks API：创建 mp.Image 并检测
            try:
                mp_img = mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=frame_rgb,
                )
                result = face_landmarker.detect(mp_img)
            except Exception as exc:
                logger.error("【诊断】face_landmarker.detect() 异常 (帧#%d): %s", frame_idx, exc)
                frame_idx += 1
                continue

            # 检查检测结果
            if result.face_landmarks is None or len(result.face_landmarks) == 0:
                frame_idx += 1
                continue

            detected_face_frames += 1
            landmarks_478 = result.face_landmarks[0]

            # 提取 478 个关键点的像素坐标
            h, w = frame.shape[:2]
            pts = np.array(
                [[lm.x * w, lm.y * h] for lm in landmarks_478],
                dtype=np.float64,
            )

            # ---- EAR 计算 ----
            ear_left = _compute_ear(pts, LEFT_EYE_IDX)
            ear_right = _compute_ear(pts, RIGHT_EYE_IDX)
            ear_left_list.append(ear_left)
            ear_right_list.append(ear_right)

            # 眨眼检测
            mean_ear = (ear_left + ear_right) / 2.0
            if mean_ear < ear_blink_threshold:
                low_ear_counter += 1
            else:
                if low_ear_counter >= blink_consecutive_frames:
                    blink_count += 1
                low_ear_counter = 0

            # ---- 头部姿态估算 ----
            pitch, yaw, roll, used_solvepnp = _estimate_head_pose(pts, w, h, camera_matrix)
            if used_solvepnp:
                solvepnp_ok += 1
            else:
                solvepnp_fallback += 1
            pitch_list.append(pitch)
            yaw_list.append(yaw)
            roll_list.append(roll)

            # ---- 眼神接触判定 ----
            if abs(yaw) < eye_contact_angle and abs(pitch) < eye_contact_angle:
                contact_frames += 1

            frame_idx += 1

        # --- 关闭资源 ---
        cap.release()
        face_landmarker.close()

        # --- 最终诊断输出 ---
        detection_rate = (detected_face_frames / processed_frames * 100.0) if processed_frames > 0 else 0.0
        logger.info("=" * 50)
        logger.info("【诊断】视觉分析最终统计:")
        logger.info("  视频总帧数 (cv2):      %d", frame_count_total)
        logger.info("  实际读取帧数:           %d", total_frames_read)
        logger.info("  送入 FaceLandmarker:    %d", processed_frames)
        logger.info("  检测到人脸帧数:         %d", detected_face_frames)
        logger.info("  人脸检测成功率:         %.1f%%", detection_rate)
        logger.info("  FaceLandmarker 初始化:  %s", "成功" if face_landmarker is not None else "失败")
        logger.info("  solvePnP 成功次数:       %d", solvepnp_ok)
        logger.info("  几何回退次数:            %d", solvepnp_fallback)
        logger.info("  debug_frame.jpg 路径:   %s",
                    os.path.join(output_dir or str(Path(__file__).resolve().parent.parent / "output"), "debug_frame.jpg"))
        if detected_face_frames > 0:
            logger.info("  pitch 范围:              %.1f ~ %.1f (均值=%.1f, std=%.1f)",
                        float(np.min(pitch_list)), float(np.max(pitch_list)),
                        float(np.mean(pitch_list)), float(np.std(pitch_list)))
            logger.info("  yaw 范围:                %.1f ~ %.1f (均值=%.1f, std=%.1f)",
                        float(np.min(yaw_list)), float(np.max(yaw_list)),
                        float(np.mean(yaw_list)), float(np.std(yaw_list)))
            logger.info("  roll 范围:               %.1f ~ %.1f (均值=%.1f, std=%.1f)",
                        float(np.min(roll_list)), float(np.max(roll_list)),
                        float(np.mean(roll_list)), float(np.std(roll_list)))
        if detection_rate == 0.0:
            logger.error("【诊断】人脸检测率为 0%% — 可能原因:")
            logger.error("  1. 视频中确实没有正面人脸（屏幕录制/幻灯片/侧面/遮挡）")
            logger.error("  2. 人脸角度超出检测范围 (偏转 >±45° 或俯仰 >±30°)")
            logger.error("  3. 光照条件极差导致检测器无法识别")
            logger.error("  4. debug_frame.jpg 可用于人工检查视频内容")
        logger.info("=" * 50)

        if detected_face_frames == 0:
            logger.warning("视觉分析: 未检测到任何人脸 (处理=%d帧)，返回默认值", processed_frames)
            output = _build_default_output(video_path)
            output["frame_count_total"] = frame_count_total
            output["duration_seconds"] = duration
            output["fps"] = float(fps)
            _save_output(output, output_dir)
            return output

        # --- 聚合统计 ---
        ear_left_arr = np.array(ear_left_list)
        ear_right_arr = np.array(ear_right_list)
        pitch_arr = np.array(pitch_list)
        yaw_arr = np.array(yaw_list)
        roll_arr = np.array(roll_list)

        contact_ratio = contact_frames / detected_face_frames if detected_face_frames > 0 else 0.0
        blink_rate = (blink_count / duration * 60.0) if duration > 0 else 0.0

        # --- 维度评分 ---
        ec_cfg = _JSON_CFG.get("eye_contact", {})
        pt_cfg = _JSON_CFG.get("posture", {})

        min_ear = ec_cfg.get("min_ear_for_contact", 0.18)
        dur_weight = ec_cfg.get("contact_duration_ratio_weight", 0.6)
        ear_weight = ec_cfg.get("ear_score_weight", 0.4)

        mean_ear_overall = float((ear_left_arr.mean() + ear_right_arr.mean()) / 2.0)
        ear_score = min(100.0, (mean_ear_overall / min_ear) * 100.0) if min_ear > 0 else 50.0
        contact_duration_score = contact_ratio * 100.0
        eye_contact_score = dur_weight * contact_duration_score + ear_weight * ear_score
        eye_contact_score = max(0.0, min(100.0, eye_contact_score))

        # 姿态得分
        stability_weight = pt_cfg.get("stability_weight", 0.7)
        alignment_weight = pt_cfg.get("alignment_weight", 0.3)
        max_sway = pt_cfg.get("max_sway_angle_deg", 15.0)

        pitch_std = float(pitch_arr.std())
        yaw_std = float(yaw_arr.std())
        pitch_mean_abs = abs(float(pitch_arr.mean()))
        yaw_mean_abs = abs(float(yaw_arr.mean()))

        angle_std = (pitch_std + yaw_std) / 2.0
        stability_score = max(0.0, 100.0 - (angle_std / max_sway) * 100.0)

        angle_deviation = (pitch_mean_abs + yaw_mean_abs) / 2.0
        alignment_score = max(0.0, 100.0 - (angle_deviation / max_sway) * 100.0)

        posture_score = stability_weight * stability_score + alignment_weight * alignment_score
        posture_score = max(0.0, min(100.0, posture_score))

        # --- 构建输出 ---
        output = {
            "source": video_path,
            "frame_count_total": frame_count_total,
            "frame_count_processed": detected_face_frames,
            "fps": float(fps),
            "duration_seconds": duration,
            "eye_contact_features": {
                "ear_left_mean": float(ear_left_arr.mean()),
                "ear_right_mean": float(ear_right_arr.mean()),
                "ear_left_std": float(ear_left_arr.std()),
                "ear_right_std": float(ear_right_arr.std()),
                "contact_ratio": float(contact_ratio),
                "blink_count": blink_count,
                "blink_rate_per_minute": float(blink_rate),
            },
            "posture_features": {
                "head_pose_angles": {
                    "pitch_mean": float(pitch_arr.mean()),
                    "pitch_std": float(pitch_arr.std()),
                    "yaw_mean": float(yaw_arr.mean()),
                    "yaw_std": float(yaw_arr.std()),
                    "roll_mean": float(roll_arr.mean()),
                    "roll_std": float(roll_arr.std()),
                },
                "posture_stability": float(stability_score / 100.0),
                "posture_uprightness": float(alignment_score / 100.0),
            },
            "dimension_scores": {
                "eye_contact_score": float(eye_contact_score),
                "posture_score": float(posture_score),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        logger.info("视觉分析完成: 检测到人脸=%d帧/%d采样帧, 眼神交流=%.1f, 姿态=%.1f",
                     detected_face_frames, processed_frames, eye_contact_score, posture_score)
        _save_output(output, output_dir)
        return output

    except Exception as exc:
        logger.error("视觉分析异常: %s，返回默认值", exc)
        output = _build_default_output(video_path)
        _save_output(output, output_dir)
        return output
    finally:
        if cap is not None and cap.isOpened():
            cap.release()
        if face_landmarker is not None:
            try:
                face_landmarker.close()
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
    filename = f"visual_features_{source_name}.json"
    output_path = os.path.join(output_dir, filename)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(features, f, ensure_ascii=False, indent=2)

    logger.info("视觉特征已写入: %s", output_path)
    return output_path
