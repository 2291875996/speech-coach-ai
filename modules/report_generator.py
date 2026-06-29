"""
报告生成器模块
读取最终评分 JSON 文件，生成中文 Markdown 格式的 AI 演讲反馈报告。
包含综合评分、四维能力分析、优势总结、改进建议和后续训练建议。
使用中文固定模板，数据缺失时输出占位文本"该维度数据暂缺"。
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("report_generator")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_handler)

# 中文报告模板（演讲反馈教练）
_REPORT_TEMPLATE = """# AI演讲反馈报告

---

## 基本信息

| 项目 | 内容 |
|------|------|
| 分析时间 | {timestamp} |
| 评分数据来源 | {source_file} |

---

## 综合评分

<div align="center">

### {overall_score} / 100

### 等级：**{grade}**

</div>

---

## 四维能力分析

| 维度 | 得分 | 权重 | 加权贡献 | 评级 |
|------|------|------|----------|------|
| 眼神交流 | {eye_contact_score:.1f} | {weight_eye:.0%} | {eye_contrib:.1f} | {eye_grade} |
| 姿态表现 | {posture_score:.1f} | {weight_posture:.0%} | {posture_contrib:.1f} | {posture_grade} |
| 手势表达 | {gesture_score:.1f} | {weight_gesture:.0%} | {gesture_contrib:.1f} | {gesture_grade} |
| 语音表达 | {speech_score:.1f} | {weight_speech:.0%} | {speech_contrib:.1f} | {speech_grade} |

---

## 眼神交流分析

{eye_contact_detail}

---

## 姿态表现分析

{posture_detail}

---

## 手势表达分析

{gesture_detail}

---

## 语音表达分析

{speech_detail}

---

## 优势总结

{strengths_section}

---

## 改进建议

{suggestions_section}

---

## 后续训练建议

{training_suggestions}

---

## 数据来源

{data_sources}

---

*本报告由 AI演讲反馈教练 自动生成 | 生成时间: {gen_timestamp}*
"""

_PLACEHOLDER = "该维度数据暂缺"


def _score_to_grade_name(score: float) -> str:
    """将分数映射为等级名称。"""
    if score >= 85:
        return "优秀 ⭐"
    if score >= 70:
        return "良好 ✓"
    if score >= 50:
        return "一般 △"
    return "需改进 ✗"


def _generate_strengths_and_suggestions(
    final_score: Dict,
    vf: Optional[Dict],
    sf: Optional[Dict],
    gf: Optional[Dict],
) -> Tuple[List[str], List[str]]:
    """基于各维度得分和特征数据，生成优势列表和改进建议列表。

    Args:
        final_score: 最终评分字典
        vf: 视觉特征字典（可选）
        sf: 语音特征字典（可选）
        gf: 手势特征字典（可选）

    Returns:
        (strengths, suggestions) 两个字符串列表
    """
    dims = final_score.get("dimension_scores", {})
    strengths: List[str] = []
    suggestions: List[str] = []

    ec = dims.get("eye_contact_score", 50)
    ps = dims.get("posture_score", 50)
    gs = dims.get("gesture_score", 50)
    ss = dims.get("speech_score", 50)

    # ---- 眼神交流 ----
    if ec >= 70:
        strengths.append("自然保持眼神交流，与观众目光接触良好")
    elif ec < 60:
        suggestions.append("建议增加与观众的目光接触，减少长时间低头或视线飘移")

    # ---- 姿态表现 ----
    if ps >= 70:
        strengths.append("站姿/坐姿端正稳定，展现了良好的自信")
    elif ps < 60:
        suggestions.append("建议保持身体稳定，避免频繁晃动或含胸驼背")

    # ---- 手势表达 ----
    if gs >= 95:
        suggestions.append("手势频率偏高，建议减少过于频繁的肢体动作，保持适度自然")
    elif gs >= 70:
        strengths.append("手势运用自然得当，有效辅助了语言表达")
    elif gs < 60:
        suggestions.append("建议自然使用开放式手势辅助表达，增强沟通感染力")

    # ---- 语音表达 ----
    if ss >= 70:
        strengths.append("语音表达流畅清晰，语言组织能力良好")
    elif ss < 60:
        suggestions.append("建议加强语音表达练习，注意语速控制和语调变化")

    # ---- 基于语音特征数据的精准建议 ----
    if sf is not None:
        sr = sf.get("speech_rate_features", {})
        wpm = sr.get("words_per_minute", 0)

        if wpm > 190:
            suggestions.append("语速偏快，建议适当放慢语速，确保听众充分理解信息")
        elif 0 < wpm < 110:
            suggestions.append("语速偏慢，建议适当加快语速，保持听众注意力")

        filler = sf.get("filler_word_analysis", {})
        filler_ratio = filler.get("filler_word_ratio", 0)
        if filler_ratio > 0.05:
            suggestions.append('填充词（如「嗯」「那个」）使用偏多，建议有意识地减少口头语，提高表达简洁度')

        pitch = sf.get("pitch_features", {})
        pitch_std = pitch.get("pitch_std_hz", 0)
        if 0 < pitch_std < 30:
            suggestions.append("语调较为平淡，建议增加语气变化以提升演讲感染力")

    # ---- 基于视觉特征数据的精准建议 ----
    if vf is not None:
        ecf = vf.get("eye_contact_features", {})
        contact_ratio = ecf.get("contact_ratio", 0)
        blink_rate = ecf.get("blink_rate_per_minute", 0)

        if contact_ratio >= 0.7:
            # 避免重复添加优势
            if not any("目光接触" in s for s in strengths):
                strengths.append("能够保持较长时间的目光接触，与观众互动感强")
        if blink_rate > 30:
            suggestions.append("眨眼频率偏高，可能与紧张情绪有关，建议通过深呼吸放松身心")

    # ---- 基于手势特征数据的精准建议 ----
    if gf is not None:
        gs_data = gf.get("gesture_statistics", {})
        gpm = gs_data.get("gestures_per_minute", 0)
        variety = gs_data.get("gesture_variety_count", 0)

        if gpm >= 5 and variety >= 3:
            if not any("手势" in s for s in strengths):
                strengths.append("手势种类丰富、频率适中，演讲表现力强")
        if 0 < gpm < 2:
            suggestions.append("手势使用偏少，建议在重点内容处适当加入手势强调")

    return strengths, suggestions


def _build_eye_contact_detail(final_score: Dict, features: Optional[Dict]) -> str:
    """生成眼神交流分析段落。"""
    if features is None:
        return _PLACEHOLDER

    ec = features.get("eye_contact_features", {})
    ear_l = ec.get("ear_left_mean", 0)
    ear_r = ec.get("ear_right_mean", 0)
    contact_ratio = ec.get("contact_ratio", 0)
    blink_rate = ec.get("blink_rate_per_minute", 0)

    lines = [
        f"- **左眼平均 EAR**: {ear_l:.3f}",
        f"- **右眼平均 EAR**: {ear_r:.3f}",
        f"- **眼神接触时间占比**: {contact_ratio:.1%}",
        f"- **眨眼频率**: {blink_rate:.1f} 次/分钟",
        "",
    ]

    if contact_ratio >= 0.7:
        lines.append("✅ 眼神接触良好，能够保持适度的目光交流，有助于建立信任感。")
    elif contact_ratio >= 0.5:
        lines.append("⚠️ 眼神接触尚可，建议增加与观众的目光交流时间，提升互动体验。")
    else:
        lines.append("❌ 眼神接触偏少，建议更加自信地面对观众，减少视线回避。")

    if blink_rate > 30:
        lines.append("⚠️ 眨眼频率偏高，可能与紧张有关，建议通过深呼吸保持放松。")
    elif blink_rate < 5:
        lines.append("⚠️ 眨眼频率偏低，长时间凝视可能给观众带来压力，建议自然眨眼。")

    return "\n".join(lines)


def _build_posture_detail(final_score: Dict, features: Optional[Dict]) -> str:
    """生成姿态分析段落。"""
    if features is None:
        return _PLACEHOLDER

    pf = features.get("posture_features", {})
    angles = pf.get("head_pose_angles", {})
    stability = pf.get("posture_stability", 0)
    upright = pf.get("posture_uprightness", 0)

    lines = [
        f"- **头部俯仰角 (Pitch) 均值**: {angles.get('pitch_mean', 0):.1f}° | 标准差: {angles.get('pitch_std', 0):.1f}°",
        f"- **头部偏转角 (Yaw) 均值**: {angles.get('yaw_mean', 0):.1f}° | 标准差: {angles.get('yaw_std', 0):.1f}°",
        f"- **头部倾斜角 (Roll) 均值**: {angles.get('roll_mean', 0):.1f}° | 标准差: {angles.get('roll_std', 0):.1f}°",
        f"- **姿态稳定性**: {stability:.1%}",
        f"- **姿态直立度**: {upright:.1%}",
        "",
    ]

    if stability >= 0.7:
        lines.append("✅ 姿态稳定，无明显晃动，展现了良好的自信心态。")
    elif stability >= 0.4:
        lines.append("⚠️ 姿态稳定性一般，身体有轻微晃动，建议保持更加稳定的站姿或坐姿。")
    else:
        lines.append("❌ 姿态不稳定，身体晃动幅度较大，可能影响专业形象和观众注意力。")

    if upright >= 0.7:
        lines.append("✅ 身姿挺拔端正，给人积极向上的印象。")
    elif upright >= 0.4:
        lines.append("⚠️ 建议保持更挺拔的姿态，避免低头含胸。")

    return "\n".join(lines)


def _build_gesture_detail(final_score: Dict, features: Optional[Dict]) -> str:
    """生成手势分析段落。"""
    if features is None:
        return _PLACEHOLDER

    gs = features.get("gesture_statistics", {})
    gc = features.get("gesture_classification", {})
    hm = features.get("hand_movement", {})

    lines = [
        f"- **手势频率**: {gs.get('gestures_per_minute', 0):.1f} 次/分钟",
        f"- **手势种类数**: {gs.get('gesture_variety_count', 0)}",
        f"- **检测到的手势总数**: {gs.get('total_gestures_detected', 0)}",
        f"- **手部出现比例**: {features.get('hand_presence_ratio', 0):.1%}",
        "",
        "**手势类型分布**:",
    ]

    for key, label in [("open_palm", "张开手掌"), ("closed_fist", "握拳"),
                        ("pointing", "指点"), ("other", "其他")]:
        info = gc.get(key, {})
        count = info.get("count", 0)
        ratio = info.get("ratio", 0)
        lines.append(f"  - {label}: {count} 次 ({ratio:.1%})")

    lines.append("")
    lines.append(f"- **手部运动平均速度**: {hm.get('mean_velocity', 0):.4f} (归一化坐标/秒)")
    lines.append(f"- **运动平滑度**: {hm.get('movement_smoothness', 0):.1%}")

    lines.append("")

    gpm = gs.get("gestures_per_minute", 0)
    variety = gs.get("gesture_variety_count", 0)

    if gpm >= 5 and variety >= 3:
        lines.append("✅ 手势运用得当，频率和多样性均表现良好，有效辅助了演讲表达。")
    elif gpm >= 2 and variety >= 2:
        lines.append("⚠️ 手势运用尚可，建议增加手势种类和自然度以增强演讲感染力。")
    else:
        lines.append("❌ 手势使用较少，建议适当运用自然开放式手势来辅助表达关键信息。")

    return "\n".join(lines)


def _build_speech_detail(final_score: Dict, features: Optional[Dict]) -> str:
    """生成语音表达分析段落。"""
    if features is None:
        return _PLACEHOLDER

    sr = features.get("speech_rate_features", {})
    pitch = features.get("pitch_features", {})
    volume = features.get("volume_features", {})
    filler = features.get("filler_word_analysis", {})

    transcription = features.get("transcription", _PLACEHOLDER)
    # 截断过长的转录文本
    if len(transcription) > 300:
        transcription = transcription[:300] + "..."

    lines = [
        f"- **语速**: {sr.get('words_per_minute', 0):.1f} 词/分钟",
        f"- **平均基频**: {pitch.get('pitch_mean_hz', 0):.1f} Hz",
        f"- **基频标准差**: {pitch.get('pitch_std_hz', 0):.1f} Hz",
        f"- **有声段占比**: {pitch.get('voiced_ratio', 0):.1%}",
        f"- **音量变异系数**: {volume.get('rms_std', 0) / max(volume.get('rms_mean', 1), 1e-8):.3f}",
        f"- **填充词数量**: {filler.get('filler_word_count', 0)}",
        f"- **填充词比例**: {filler.get('filler_word_ratio', 0):.1%}",
        "",
        f"**转录片段**: {transcription}",
        "",
    ]

    wpm = sr.get("words_per_minute", 0)
    filler_ratio = filler.get("filler_word_ratio", 0)

    if 130 <= wpm <= 170:
        lines.append("✅ 语速适中，节奏感好，信息传达清晰。")
    elif 110 <= wpm <= 190:
        lines.append("⚠️ 语速在可接受范围内，但可进一步优化节奏感以提升听众体验。")
    elif wpm > 190:
        lines.append("❌ 语速偏快，可能让听众难以跟上思路，建议适当放慢。")
    else:
        lines.append("❌ 语速偏慢，可能导致听众注意力涣散，建议适当加快语速。")

    if filler_ratio <= 0.02:
        lines.append("✅ 填充词控制良好，表达简洁有力，专业度高。")
    elif filler_ratio <= 0.05:
        lines.append("⚠️ 存在一定数量的填充词，建议有意识地减少口头语。")
    else:
        lines.append("❌ 填充词过多，影响表达的专业性和流畅度，建议多加练习减少依赖。")

    if pitch.get("pitch_std_hz", 0) >= 30:
        lines.append("✅ 语调有起伏变化，表达富有感染力。")
    else:
        lines.append("⚠️ 语调较为平淡，建议增加语气变化以提升演讲的吸引力和感染力。")

    return "\n".join(lines)


def _build_strengths_section(strengths: List[str]) -> str:
    """生成优势总结段落。"""
    if not strengths:
        return "本次演讲各维度表现均衡，继续努力保持！"

    lines = ["以下是在本次演讲中表现突出的优势：", ""]
    for i, s in enumerate(strengths, 1):
        lines.append(f"{i}. ✅ {s}")
    return "\n".join(lines)


def _build_suggestions_section(suggestions: List[str]) -> str:
    """生成改进建议段落。"""
    if not suggestions:
        return "本次演讲表现良好，暂无特别需要改进的方面。"

    lines = ["针对本次演讲分析，提出以下改进建议：", ""]
    for i, s in enumerate(suggestions, 1):
        lines.append(f"{i}. 📌 {s}")
    return "\n".join(lines)


def _build_training_suggestions(final_score: Dict) -> str:
    """根据各维度得分生成后续训练建议。

    基于最弱维度给出针对性的日常训练方案。
    """
    dims = final_score.get("dimension_scores", {})
    ec = dims.get("eye_contact_score", 50)
    ps = dims.get("posture_score", 50)
    gs = dims.get("gesture_score", 50)
    ss = dims.get("speech_score", 50)

    scores = [
        ("眼神交流", ec),
        ("姿态表现", ps),
        ("手势表达", gs),
        ("语音表达", ss),
    ]
    scores.sort(key=lambda x: x[1])

    lines = [
        "为持续提升公开演讲能力，建议按照以下计划进行日常训练：",
        "",
    ]

    # 核心建议：针对最弱维度
    weakest_name, weakest_score = scores[0]
    lines.append(f"### 重点突破：{weakest_name}（{weakest_score:.0f} 分）")
    lines.append("")

    training_plans = {
        "眼神交流": [
            "- **镜子练习**：每天对着镜子演讲 5 分钟，有意识地注视镜中自己的眼睛",
            "- **录制回顾**：用手机录制演讲过程，回看时重点关注视线方向",
            "- **三角扫视法**：练习在观众左、中、右三个区域轮流停留目光，每次 2-3 秒",
            "- **渐进练习**：先与 1-2 位朋友练习对视，逐步增加人数",
        ],
        "姿态表现": [
            "- **靠墙站立**：每天靠墙站立 5 分钟，脚跟、臀部、肩胛骨、后脑勺贴墙",
            "- **录像纠正**：录制自己的演讲姿态，与优秀演讲者对比改进",
            "- **核心训练**：通过平板支撑等核心肌群训练提升身体稳定性",
            "- **模拟演练**：在全身镜前练习，注意肩膀放松、收腹挺胸",
        ],
        "手势表达": [
            "- **手势分类练习**：分别练习开放式手掌、列举手势、强调手势等基本手势",
            "- **无实物表演**：练习用肢体语言描述物体大小、形状、位置",
            "- **演讲标注法**：在讲稿上标注适合加入手势的关键词位置",
            "- **跟练模仿**：选择一个优秀 TED 演讲视频，模仿其手势运用",
        ],
        "语音表达": [
            "- **朗读训练**：每天大声朗读文章 10 分钟，注意语速、语调和停顿",
            "- **录音自评**：录制自己的演讲音频，标记出填充词出现的位置",
            "- **变速练习**：同一段内容用不同语速（慢速/正常/快速）各讲一遍",
            "- **绕口令练习**：通过绕口令提升口腔灵活度和发音清晰度",
        ],
    }

    plan = training_plans.get(weakest_name, training_plans["语音表达"])
    lines.extend(plan)

    # 综合训练建议
    lines.append("")
    lines.append("### 综合训练")
    lines.append("")
    lines.append("- **模拟演练**：每周进行 1-2 次完整模拟演讲，使用本系统定期追踪进步")
    lines.append("- **演讲俱乐部**：加入演讲俱乐部（如 Toastmasters），在实践中持续提升")
    lines.append("- **多样性练习**：尝试不同主题、不同场合的演讲，拓展表达能力")
    lines.append("- **心态调整**：演讲前进行深呼吸和积极心理暗示，保持自然放松的状态")
    lines.append("- **内容为王**：持续提升演讲内容的逻辑性和条理性，好内容是好演讲的基础")

    return "\n".join(lines)


def generate(
    score_json_path: str,
    visual_features_path: Optional[str] = None,
    speech_features_path: Optional[str] = None,
    gesture_features_path: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> str:
    """读取评分 JSON 文件，生成中文 Markdown 演讲反馈报告。

    Args:
        score_json_path: 最终评分 JSON 文件路径
        visual_features_path: 视觉特征 JSON 文件路径（可选，用于详细分析）
        speech_features_path: 语音特征 JSON 文件路径（可选，用于详细分析）
        gesture_features_path: 手势特征 JSON 文件路径（可选，用于详细分析）
        output_dir: 报告输出目录

    Returns:
        生成的 Markdown 报告文本
    """
    logger.info("开始生成演讲反馈报告")

    # 读取评分数据
    if not os.path.isfile(score_json_path):
        logger.error("评分文件不存在: %s", score_json_path)
        report = f"# 错误\n\n评分文件不存在: {score_json_path}\n\n{_PLACEHOLDER}"
        _save_report(report, output_dir)
        return report

    with open(score_json_path, "r", encoding="utf-8") as f:
        final_score = json.load(f)

    # 尝试加载各维度特征文件
    def _load_optional(path: Optional[str]) -> Optional[Dict]:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    vf = _load_optional(visual_features_path)
    sf = _load_optional(speech_features_path)
    gf = _load_optional(gesture_features_path)

    dims = final_score.get("dimension_scores", {})
    weights = final_score.get("weights_used", {})

    ec = dims.get("eye_contact_score", 0)
    ps = dims.get("posture_score", 0)
    gs = dims.get("gesture_score", 0)
    ss = dims.get("speech_score", 0)

    we = weights.get("eye_contact", 0.30)
    wp = weights.get("posture", 0.25)
    wg = weights.get("gesture", 0.20)
    ws = weights.get("speech", 0.25)

    # 生成优势和改进建议
    strengths, suggestions = _generate_strengths_and_suggestions(
        final_score, vf, sf, gf
    )

    # 数据来源描述
    data_sources_lines = [
        f"- 最终评分: `{score_json_path}`",
    ]
    if visual_features_path:
        data_sources_lines.append(f"- 视觉特征: `{visual_features_path}`")
    if speech_features_path:
        data_sources_lines.append(f"- 语音特征: `{speech_features_path}`")
    if gesture_features_path:
        data_sources_lines.append(f"- 手势特征: `{gesture_features_path}`")

    # 填充模板
    report = _REPORT_TEMPLATE.format(
        timestamp=final_score.get("timestamp", "未知"),
        source_file=os.path.basename(score_json_path),
        overall_score=f"{final_score.get('overall_score', 0):.1f}",
        grade=final_score.get("grade", "未知"),
        eye_contact_score=ec,
        posture_score=ps,
        gesture_score=gs,
        speech_score=ss,
        weight_eye=we,
        weight_posture=wp,
        weight_gesture=wg,
        weight_speech=ws,
        eye_contrib=ec * we,
        posture_contrib=ps * wp,
        gesture_contrib=gs * wg,
        speech_contrib=ss * ws,
        eye_grade=_score_to_grade_name(ec),
        posture_grade=_score_to_grade_name(ps),
        gesture_grade=_score_to_grade_name(gs),
        speech_grade=_score_to_grade_name(ss),
        eye_contact_detail=_build_eye_contact_detail(final_score, vf),
        posture_detail=_build_posture_detail(final_score, vf),
        gesture_detail=_build_gesture_detail(final_score, gf),
        speech_detail=_build_speech_detail(final_score, sf),
        strengths_section=_build_strengths_section(strengths),
        suggestions_section=_build_suggestions_section(suggestions),
        training_suggestions=_build_training_suggestions(final_score),
        data_sources="\n".join(data_sources_lines),
        gen_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # 若存在警告信息，追加到报告末尾
    warnings = final_score.get("warnings", [])
    if warnings:
        report += "\n\n---\n\n## 警告信息\n\n"
        for w in warnings:
            report += f"- ⚠️ {w}\n"

    _save_report(report, output_dir)
    logger.info("演讲反馈报告生成完成")
    return report


def _save_report(report: str, output_dir: Optional[str] = None) -> str:
    """将报告写入 Markdown 文件。"""
    if output_dir is None:
        output_dir = str(
            Path(__file__).resolve().parent.parent / "output" / "reports"
        )
    os.makedirs(output_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"speech_report_{ts}.md"
    output_path = os.path.join(output_dir, filename)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info("演讲反馈报告已写入: %s", output_path)
    return output_path
