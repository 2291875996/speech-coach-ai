"""
AI 演讲反馈教练仪表盘 — 产品级界面
=====================================
仅负责前端展示，不修改后端 pipeline 结构。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import threading

import numpy as np
import pandas as pd
import streamlit as st

# ============================================================================
# 页面配置 — 必须放在第一条 st.* 调用之前
# ============================================================================
st.set_page_config(
    page_title="AI 演讲反馈教练 | Speech Coach",
    page_icon="🎤",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": "AI 演讲反馈教练系统 v8.0 | 基于多模态人工智能的演讲分析平台",
    },
)

# ============================================================================
# 全局常量
# ============================================================================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_OUTPUT = _PROJECT_ROOT / "output"

# 主题色板
PALETTE = {
    "primary":   "#4F46E5",  # Indigo
    "secondary": "#7C3AED",  # Purple
    "accent":    "#06B6D4",  # Cyan
    "success":   "#10B981",  # Emerald
    "warning":   "#F59E0B",  # Amber
    "danger":    "#EF4444",  # Red
    "dark":      "#0F172A",  # Slate 900
    "card_bg":   "#1E293B",  # Slate 800
    "surface":   "#334155",  # Slate 700
    "text":      "#F1F5F9",  # Slate 100
    "muted":     "#94A3B8",  # Slate 400
}

DIM_LABELS = {
    "eye_contact_score": ("👁️", "眼神交流"),
    "posture_score":      ("🧍", "姿态表现"),
    "gesture_score":      ("🤝", "手势表达"),
    "speech_score":       ("🎤", "语音表达"),
}

DIM_ORDER = ["eye_contact_score", "posture_score", "gesture_score", "speech_score"]


# ============================================================================
# CSS 注入
# ============================================================================

def _inject_css() -> None:
    """注入全局自定义 CSS。"""
    st.markdown(f"""
    <style>
    /* ---------- 全局 ---------- */
    .stApp {{
        background: linear-gradient(135deg, {PALETTE["dark"]} 0%, #1a1035 50%, #0f2027 100%);
    }}
    section[data-testid="stSidebar"] {{
        background: rgba(15, 23, 42, 0.95);
        border-right: 1px solid rgba(148, 163, 184, 0.1);
    }}

    /* ---------- 卡片 ---------- */
    .score-hero {{
        background: linear-gradient(135deg, {PALETTE["primary"]}, {PALETTE["secondary"]});
        border-radius: 16px;
        padding: 2rem 2.5rem;
        text-align: center;
        color: white;
        box-shadow: 0 8px 32px rgba(79, 70, 229, 0.35);
        margin-bottom: 1rem;
    }}
    .score-hero .big-number {{
        font-size: 5rem;
        font-weight: 800;
        line-height: 1;
        letter-spacing: -0.03em;
    }}
    .score-hero .grade-badge {{
        display: inline-block;
        background: rgba(255,255,255,0.2);
        backdrop-filter: blur(8px);
        padding: 0.3rem 1.5rem;
        border-radius: 999px;
        font-size: 1.2rem;
        font-weight: 600;
        margin-top: 0.5rem;
    }}

    .dim-card {{
        background: {PALETTE["card_bg"]};
        border-radius: 12px;
        padding: 1.25rem 1.5rem;
        border: 1px solid rgba(148, 163, 184, 0.08);
        box-shadow: 0 4px 16px rgba(0,0,0,0.2);
        transition: transform 0.2s, box-shadow 0.2s;
        height: 100%;
    }}
    .dim-card:hover {{
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(79, 70, 229, 0.2);
        border-color: rgba(79, 70, 229, 0.3);
    }}
    .dim-card .dim-icon {{
        font-size: 1.5rem;
        margin-bottom: 0.25rem;
    }}
    .dim-card .dim-name {{
        color: {PALETTE["muted"]};
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 0.25rem;
    }}
    .dim-card .dim-score {{
        font-size: 2rem;
        font-weight: 700;
        color: {PALETTE["text"]};
        line-height: 1.2;
    }}
    .dim-card .dim-grade {{
        font-size: 0.75rem;
        font-weight: 500;
        margin-top: 0.15rem;
    }}

    /* ---------- 进度条 ---------- */
    .progress-bar-bg {{
        background: rgba(148, 163, 184, 0.15);
        border-radius: 999px;
        height: 6px;
        margin-top: 0.4rem;
        overflow: hidden;
    }}
    .progress-bar-fill {{
        height: 100%;
        border-radius: 999px;
        transition: width 0.6s ease;
    }}

    /* ---------- 状态标签 ---------- */
    .status-dot {{
        display: inline-block;
        width: 8px; height: 8px;
        border-radius: 50%;
        margin-right: 6px;
    }}
    .status-dot.ok {{ background: {PALETTE["success"]}; }}
    .status-dot.running {{ background: {PALETTE["warning"]}; animation: pulse 1.2s infinite; }}
    .status-dot.waiting {{ background: {PALETTE["muted"]}; }}

    @keyframes pulse {{
        0%, 100% {{ opacity: 1; }}
        50% {{ opacity: 0.35; }}
    }}

    /* ---------- 文件信息 ---------- */
    .file-info {{
        background: {PALETTE["card_bg"]};
        border-radius: 12px;
        padding: 0.75rem 1rem;
        font-size: 0.8rem;
        color: {PALETTE["muted"]};
        border: 1px solid rgba(148, 163, 184, 0.06);
        margin-top: 0.5rem;
    }}

    /* ---------- 分区标题 ---------- */
    .section-title {{
        font-size: 1rem;
        font-weight: 700;
        color: {PALETTE["text"]};
        letter-spacing: 0.02em;
        margin-bottom: 0.75rem;
        padding-bottom: 0.5rem;
        border-bottom: 2px solid rgba(79, 70, 229, 0.4);
    }}

    /* ---------- 优势 / 建议面板 ---------- */
    .insight-box {{
        background: {PALETTE["card_bg"]};
        border-radius: 12px;
        padding: 1.25rem 1.5rem;
        border: 1px solid rgba(148, 163, 184, 0.08);
        margin-bottom: 0.6rem;
    }}
    .insight-box.strength {{
        border-left: 3px solid {PALETTE["success"]};
    }}
    .insight-box.suggestion {{
        border-left: 3px solid {PALETTE["warning"]};
    }}
    .insight-box .insight-label {{
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.05em;
        margin-bottom: 0.4rem;
    }}
    .insight-box .insight-text {{
        color: {PALETTE["text"]};
        font-size: 0.9rem;
        line-height: 1.5;
    }}

    /* ---------- 滚动条 ---------- */
    ::-webkit-scrollbar {{ width: 6px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}
    ::-webkit-scrollbar-thumb {{ background: rgba(148,163,184,0.25); border-radius: 3px; }}

    /* ---------- 按钮 ---------- */
    .stButton > button {{
        border-radius: 10px !important;
        font-weight: 600 !important;
        transition: all 0.2s !important;
    }}
    .stButton > button:hover {{
        transform: translateY(-1px);
    }}
    </style>
    """, unsafe_allow_html=True)


# ============================================================================
# 工具函数
# ============================================================================

def _scan_output_dir(output_dir: Path) -> Dict[str, Optional[Path]]:
    """扫描输出目录，按修改时间倒序取最新文件。"""
    result: Dict[str, Optional[Path]] = {
        "visual": None, "speech": None, "gesture": None,
        "score": None, "report": None,
    }
    feats = output_dir / "features"
    reps = output_dir / "reports"
    if feats.is_dir():
        for f in sorted(feats.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not result["visual"] and f.name.startswith("visual_features_"):
                result["visual"] = f
            if not result["speech"] and f.name.startswith("speech_features_"):
                result["speech"] = f
            if not result["gesture"] and f.name.startswith("gesture_features_"):
                result["gesture"] = f
    if reps.is_dir():
        for f in sorted(reps.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not result["score"] and f.name.startswith("final_score_"):
                result["score"] = f
            if not result["report"] and f.suffix == ".md":
                result["report"] = f
    return result


def _load_json(path: Optional[Path]) -> Optional[Dict]:
    if path is None or not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _run_pipeline(video_path: str, output_dir: Path) -> subprocess.Popen:
    main_py = _PROJECT_ROOT / "main.py"
    return subprocess.Popen(
        [sys.executable, str(main_py), video_path, "-o", str(output_dir)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )


def _read_stdout_async(proc: subprocess.Popen, logs: list):
    """后台线程：非阻塞读取子进程 stdout / stderr。"""
    for line in iter(proc.stdout.readline, ""):
        if line:
            logs.append(line.strip())
    # stdout 关闭后读取 stderr 剩余
    for line in iter(proc.stderr.readline, ""):
        if line:
            logs.append("[STDERR] " + line.strip())


def _get_level(score: float) -> Tuple[str, str, str]:
    """(等级标签, 颜色hex, emoji)"""
    if score >= 85:
        return "优秀", PALETTE["success"], "⭐"
    if score >= 70:
        return "良好", PALETTE["accent"], "✅"
    if score >= 50:
        return "一般", PALETTE["warning"], "⚠️"
    return "需改进", PALETTE["danger"], "📉"


# ============================================================================
# 演讲建议引擎（Dashboard 端计算，不依赖后端）
# ============================================================================

def _compute_insights(score_data: Dict, sf: Optional[Dict], vf: Optional[Dict],
                      gf: Optional[Dict]) -> Tuple[List[str], List[str]]:
    """基于评分和特征数据计算优势和改进建议。

    Returns:
        (strengths, suggestions) 两个列表
    """
    dims = score_data.get("dimension_scores", {})
    strengths: List[str] = []
    suggestions: List[str] = []

    ec = dims.get("eye_contact_score", 50)
    ps = dims.get("posture_score", 50)
    gs = dims.get("gesture_score", 50)
    ss = dims.get("speech_score", 50)

    # 眼神交流
    if ec >= 70:
        strengths.append("自然保持眼神交流，与观众目光接触良好")
    elif ec < 60:
        suggestions.append("增加与观众的目光接触，减少长时间低头或视线飘移")

    # 姿态表现
    if ps >= 70:
        strengths.append("站姿/坐姿端正稳定，展现良好的自信")
    elif ps < 60:
        suggestions.append("保持身体稳定，避免频繁晃动或含胸驼背")

    # 手势表达
    if gs >= 95:
        suggestions.append("手势频率偏高，减少过于频繁的肢体动作")
    elif gs >= 70:
        strengths.append("手势运用自然得当，有效辅助表达")
    elif gs < 60:
        suggestions.append("自然使用开放式手势辅助表达，增强感染力")

    # 语音表达
    if ss >= 70:
        strengths.append("语音表达流畅清晰，语言组织能力良好")
    elif ss < 60:
        suggestions.append("加强语音表达练习，注意语速控制和语调变化")

    # 基于语音特征细节
    if sf is not None:
        sr = sf.get("speech_rate_features", {})
        wpm = sr.get("words_per_minute", 0)
        if wpm > 190:
            suggestions.append("语速偏快，适当放慢以确保听众理解")
        elif 0 < wpm < 110:
            suggestions.append("语速偏慢，适当加快以保持听众注意力")

        filler = sf.get("filler_word_analysis", {})
        if filler.get("filler_word_ratio", 0) > 0.05:
            suggestions.append('减少「嗯」「那个」等口头语，提高表达简洁度')

        pitch = sf.get("pitch_features", {})
        if 0 < pitch.get("pitch_std_hz", 0) < 30:
            suggestions.append("增加语调变化以提升演讲感染力")

    # 基于视觉特征细节
    if vf is not None:
        ecf = vf.get("eye_contact_features", {})
        if ecf.get("contact_ratio", 0) >= 0.7:
            if not any("目光接触" in s for s in strengths):
                strengths.append("保持较长时间的目光接触，与观众互动感强")
        if ecf.get("blink_rate_per_minute", 0) > 30:
            suggestions.append("通过深呼吸放松身心，减少紧张性眨眼")

    # 基于手势特征细节
    if gf is not None:
        gs_data = gf.get("gesture_statistics", {})
        gpm = gs_data.get("gestures_per_minute", 0)
        variety = gs_data.get("gesture_variety_count", 0)
        if gpm >= 5 and variety >= 3:
            if not any("手势" in s for s in strengths):
                strengths.append("手势种类丰富，演讲表现力强")
        if 0 < gpm < 2:
            suggestions.append("在重点内容处适当加入手势强调")

    return strengths, suggestions


# ============================================================================
# Plotly 图表
# ============================================================================

def _plotly_radar(dims: Dict[str, float]) -> None:
    import plotly.graph_objects as go

    labels = [DIM_LABELS[k][1] for k in DIM_ORDER]
    values = [dims.get(k, 0) for k in DIM_ORDER]
    values.append(values[0])  # close

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values,
        theta=labels + [labels[0]],
        fill="toself",
        fillcolor="rgba(79, 70, 229, 0.3)",
        line=dict(color=PALETTE["primary"], width=3),
        marker=dict(size=8, color=PALETTE["primary"]),
        name="能力得分",
        hovertemplate="%{theta}: <b>%{r:.1f}</b> 分<extra></extra>",
    ))
    fig.update_layout(
        polar=dict(
            radialaxis=dict(range=[0, 100], tickfont=dict(color=PALETTE["muted"], size=10),
                            gridcolor="rgba(148,163,184,0.12)"),
            angularaxis=dict(tickfont=dict(color=PALETTE["text"], size=12),
                             gridcolor="rgba(148,163,184,0.12)"),
            bgcolor="rgba(0,0,0,0)",
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=40, r=40, t=30, b=30),
        height=380,
        showlegend=False,
    )
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})


def _plotly_bar(dims: Dict[str, float]) -> None:
    import plotly.graph_objects as go

    labels = [DIM_LABELS[k][1] for k in DIM_ORDER]
    values = [dims.get(k, 0) for k in DIM_ORDER]
    bar_colors = [PALETTE["primary"], PALETTE["success"], PALETTE["warning"], PALETTE["accent"]]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=values,
        marker=dict(
            color=bar_colors,
            line=dict(color="rgba(255,255,255,0.15)", width=1),
            cornerradius=8,
        ),
        text=[f"{v:.1f}" for v in values],
        textposition="outside",
        textfont=dict(color=PALETTE["text"], size=14, family="sans-serif"),
        hovertemplate="%{x}: <b>%{y:.1f}</b> 分<extra></extra>",
    ))
    # 阈值线
    for y, label, color in [(85, "优秀线", PALETTE["success"]),
                              (70, "良好线", PALETTE["accent"]),
                              (50, "一般线", PALETTE["warning"])]:
        fig.add_hline(y=y, line_dash="dash", line_color=color, line_width=1,
                       opacity=0.5, annotation_text=label,
                       annotation_position="right", annotation_font_color=color)

    fig.update_layout(
        yaxis=dict(range=[0, 110], tickfont=dict(color=PALETTE["muted"]),
                    gridcolor="rgba(148,163,184,0.1)", zerolinecolor="rgba(148,163,184,0.15)"),
        xaxis=dict(tickfont=dict(color=PALETTE["text"], size=12)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=30, b=20), height=380,
        showlegend=False,
    )
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})


def _plotly_weights(weights: Dict[str, float]) -> None:
    import plotly.graph_objects as go

    labels = [DIM_LABELS[k][1] for k in DIM_ORDER]
    values = [weights.get(k, 0) for k in DIM_ORDER]
    pie_colors = [PALETTE["primary"], PALETTE["secondary"], PALETTE["accent"], PALETTE["success"]]

    fig = go.Figure()
    fig.add_trace(go.Pie(
        labels=labels, values=values,
        marker=dict(colors=pie_colors, line=dict(color="rgba(0,0,0,0.3)", width=1)),
        textinfo="label+percent",
        textfont=dict(color=PALETTE["text"], size=12),
        hole=0.55,
        hovertemplate="%{label}: <b>%{percent}</b><extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=10, b=10), height=380,
        showlegend=False,
    )
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})


# ============================================================================
# UI 组件
# ============================================================================

def _render_hero(overall: float, grade: str, color: str) -> None:
    """渲染综合得分卡片。"""
    st.markdown(f"""
    <div class="score-hero">
        <div style="font-size:0.85rem;opacity:0.8;letter-spacing:0.1em;text-transform:uppercase;">综合得分</div>
        <div class="big-number">{overall:.1f}</div>
        <div style="font-size:0.9rem;opacity:0.75;">满分 100</div>
        <div class="grade-badge">{grade}</div>
    </div>
    """, unsafe_allow_html=True)


def _render_dim_card(key: str, score: float) -> None:
    """渲染单个维度评分卡片。"""
    icon, name = DIM_LABELS[key]
    level, color, emoji = _get_level(score)
    st.markdown(f"""
    <div class="dim-card">
        <div class="dim-icon">{icon}</div>
        <div class="dim-name">{name}</div>
        <div class="dim-score">{score:.1f}</div>
        <div class="dim-grade" style="color:{color};">{emoji} {level}</div>
        <div class="progress-bar-bg">
            <div class="progress-bar-fill" style="width:{score}%;background:{color};"></div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def _render_status_row(paths: Dict[str, Optional[Path]], running: bool) -> None:
    """渲染模块运行状态行。"""
    items = [
        ("视觉分析",  "visual"),
        ("语音分析",  "speech"),
        ("手势分析",  "gesture"),
        ("综合评分",  "score"),
        ("分析报告",  "report"),
    ]
    cols = st.columns(len(items))
    for col, (label, key) in zip(cols, items):
        p = paths.get(key)
        with col:
            if p is not None:
                dot = "ok"
                text = f"<span class='status-dot {dot}'></span> {label}<br><small style='color:{PALETTE['success']};'>✅ 完成</small>"
            elif running:
                dot = "running"
                text = f"<span class='status-dot {dot}'></span> {label}<br><small style='color:{PALETTE['warning']};'>⏳ 进行中</small>"
            else:
                dot = "waiting"
                text = f"<span class='status-dot {dot}'></span> {label}<br><small style='color:{PALETTE['muted']};'>○ 等待中</small>"
            st.markdown(text, unsafe_allow_html=True)


def _render_strengths_panel(strengths: List[str]) -> None:
    """渲染优势分析面板。"""
    if not strengths:
        st.info("各维度表现均衡，暂无特别突出的优势项。")
        return

    for s in strengths:
        st.markdown(f"""
        <div class="insight-box strength">
            <div class="insight-label" style="color:{PALETTE['success']};">✅ 优势</div>
            <div class="insight-text">{s}</div>
        </div>
        """, unsafe_allow_html=True)


def _render_suggestions_panel(suggestions: List[str]) -> None:
    """渲染改进建议面板。"""
    if not suggestions:
        st.success("本次演讲表现良好，暂无特别需要改进的方面。")
        return

    for s in suggestions:
        st.markdown(f"""
        <div class="insight-box suggestion">
            <div class="insight-label" style="color:{PALETTE['warning']};">📌 改进建议</div>
            <div class="insight-text">{s}</div>
        </div>
        """, unsafe_allow_html=True)


# ============================================================================
# Session State
# ============================================================================

DEFAULT_STATE = {
    "process": None,
    "running": False,
    "output_dir": None,
    "logs": [],
    "log_thread": None,
    "pipeline_error": False,
    "pipeline_exit_code": None,
    "video_name": "",
    "video_size_mb": 0.0,
}

for _k, _v in DEFAULT_STATE.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ============================================================================
# 主渲染
# ============================================================================

def main() -> None:
    _inject_css()

    # ============================
    # 侧边栏
    # ============================
    with st.sidebar:
        st.markdown(f"""
        <div style="text-align:center;margin-bottom:1rem;">
            <div style="font-size:2.5rem;">🎤</div>
            <div style="font-size:1.1rem;font-weight:700;color:{PALETTE['text']};">AI 演讲反馈教练</div>
            <div style="font-size:0.7rem;color:{PALETTE['muted']};">AI Speech Coach</div>
        </div>
        """, unsafe_allow_html=True)
        st.divider()

        # 上传
        st.markdown(f"<div class='section-title'>📁 视频上传</div>", unsafe_allow_html=True)
        uploaded = st.file_uploader(
            "选择演讲视频文件",
            type=["mp4", "mov", "avi", "webm"],
            label_visibility="collapsed",
        )

        video_path: Optional[str] = None
        if uploaded is not None:
            tmp_dir = Path(tempfile.gettempdir()) / "speech_coach_uploads"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            save_path = tmp_dir / f"upload_{uploaded.name}"
            with open(save_path, "wb") as f:
                f.write(uploaded.read())
            video_path = str(save_path)
            st.session_state.video_name = uploaded.name
            st.session_state.video_size_mb = uploaded.size / (1024 * 1024)
            st.markdown(f"""
            <div class="file-info">
                📄 {uploaded.name}<br>
                📦 {st.session_state.video_size_mb:.1f} MB
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # 开始按钮
        do_analyze = st.button(
            "🚀 开始演讲分析",
            type="primary",
            use_container_width=True,
            disabled=(video_path is None or st.session_state.running),
        )

        if do_analyze and video_path:
            st.session_state.running = True
            st.session_state.logs = []
            st.session_state.pipeline_error = False
            st.session_state.pipeline_exit_code = None
            output_dir = _DEFAULT_OUTPUT
            st.session_state.output_dir = output_dir

            # 清理旧输出
            for sub in ["features", "reports"]:
                d = output_dir / sub
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)

            proc = _run_pipeline(video_path, output_dir)
            st.session_state.process = proc
            # 启动后台线程非阻塞读取日志
            t = threading.Thread(
                target=_read_stdout_async,
                args=(proc, st.session_state.logs),
                daemon=True,
            )
            t.start()
            st.session_state.log_thread = t
            # 立即刷新进入进度模式
            st.rerun()

        # --- 分析进行中的进度展示（非阻塞，按文件检测） ---
        if st.session_state.running:
            st.divider()
            st.markdown(f"<div class='section-title'>📊 分析进度</div>", unsafe_allow_html=True)

            proc = st.session_state.process
            paths = _scan_output_dir(st.session_state.output_dir or _DEFAULT_OUTPUT)

            # 按文件存在性检测每个步骤
            step_status = [
                ("👁️ 视觉分析", paths["visual"] is not None),
                ("🎤 语音分析", paths["speech"] is not None),
                ("🤝 手势分析", paths["gesture"] is not None),
                ("📊 综合评分", paths["score"] is not None),
                ("📝 报告生成", paths["report"] is not None),
            ]
            done_count = sum(1 for _, ok in step_status if ok)
            total_steps = len(step_status)
            pct = done_count / total_steps

            # 进度条
            if done_count == 0:
                prog_text = "正在启动分析引擎…"
            elif done_count < total_steps:
                current = [s for s, ok in step_status if not ok][0]
                prog_text = f"正在进行: {current}"
            else:
                prog_text = "分析完成，正在汇总结果…"
            st.progress(pct, text=prog_text)

            # 每步状态图标
            for label, ok in step_status:
                if ok:
                    st.success(label)
                else:
                    st.info(label)

            # 检查进程是否已结束
            if proc is not None and proc.poll() is not None:
                exit_code = proc.returncode
                st.session_state.pipeline_exit_code = exit_code
                # 等待日志线程收尾
                if st.session_state.log_thread is not None:
                    st.session_state.log_thread.join(timeout=2)
                st.session_state.process = None

                if exit_code != 0:
                    st.session_state.pipeline_error = True
                    st.error(f"⚠️ 分析流水线异常退出（退出码: {exit_code}）")
                    # 从日志中提取 ERROR 行
                    error_lines = [l for l in st.session_state.logs if "ERROR" in l or "错误" in l or "失败" in l]
                    if error_lines:
                        with st.expander("🔍 查看错误详情", expanded=True):
                            for line in error_lines[-10:]:
                                st.code(line, language="text")
                else:
                    st.session_state.pipeline_error = False

                st.session_state.running = False
                time.sleep(0.3)
                st.rerun()

            # 仍在运行：定时自动刷新
            time.sleep(1)
            st.rerun()

        st.divider()
        st.caption(f"© 2026 AI 演讲反馈教练 v8.0 | {datetime.now().strftime('%H:%M:%S')}")

    # ============================
    # 主区域
    # ============================
    paths = _scan_output_dir(st.session_state.output_dir or _DEFAULT_OUTPUT)
    score_data = _load_json(paths["score"])

    # 加载特征数据（用于洞察计算）
    vf_data = _load_json(paths["visual"])
    sf_data = _load_json(paths["speech"])
    gf_data = _load_json(paths["gesture"])

    # ---- Tabs ----
    t_overview, t_process, t_report, t_debug = st.tabs([
        "📋 分析概览", "⚙️ 过程监控", "📝 完整报告", "🔧 调试数据"
    ])

    # ============================
    # Tab 1: 分析概览
    # ============================
    with t_overview:
        if score_data:
            overall = score_data.get("overall_score", 0)
            dims = score_data.get("dimension_scores", {})
            weights = score_data.get("weights_used", {})
            grade, color, _ = _get_level(overall)

            # Hero 卡片
            _render_hero(overall, grade, color)

            st.markdown("<br>", unsafe_allow_html=True)

            # 四维能力卡片
            st.markdown(f"<div class='section-title'>四维能力评估</div>", unsafe_allow_html=True)
            c1, c2, c3, c4 = st.columns(4)
            for col, key in zip([c1, c2, c3, c4], DIM_ORDER):
                with col:
                    _render_dim_card(key, dims.get(key, 0))

            st.markdown("<br>", unsafe_allow_html=True)

            # 图表区
            st.markdown(f"<div class='section-title'>可视化分析</div>", unsafe_allow_html=True)
            cc1, cc2, cc3 = st.columns([1, 1, 1])
            with cc1:
                st.markdown(f"<p style='color:{PALETTE['muted']};font-size:0.8rem;text-align:center;'>🎯 四维能力雷达图</p>",
                            unsafe_allow_html=True)
                try:
                    _plotly_radar(dims)
                except Exception as exc:
                    st.warning(f"雷达图渲染失败: {exc}")

            with cc2:
                st.markdown(f"<p style='color:{PALETTE['muted']};font-size:0.8rem;text-align:center;'>📊 维度得分对比</p>",
                            unsafe_allow_html=True)
                try:
                    _plotly_bar(dims)
                except Exception as exc:
                    st.warning(f"柱状图渲染失败: {exc}")

            with cc3:
                st.markdown(f"<p style='color:{PALETTE['muted']};font-size:0.8rem;text-align:center;'>🍩 评分权重分布</p>",
                            unsafe_allow_html=True)
                try:
                    _plotly_weights(weights)
                except Exception as exc:
                    st.warning(f"权重图渲染失败: {exc}")

            st.markdown("<br>", unsafe_allow_html=True)

            # ---- 新增：优势分析 & 改进建议 ----
            strengths, suggestions = _compute_insights(
                score_data, sf_data, vf_data, gf_data
            )

            col_left, col_right = st.columns(2)
            with col_left:
                st.markdown(f"<div class='section-title'>🌟 优势分析</div>", unsafe_allow_html=True)
                _render_strengths_panel(strengths)

            with col_right:
                st.markdown(f"<div class='section-title'>💡 改进建议</div>", unsafe_allow_html=True)
                _render_suggestions_panel(suggestions)

        else:
            st.markdown(f"""
            <div style="text-align:center;padding:4rem 1rem;">
                <div style="font-size:4rem;">🎤</div>
                <div style="font-size:1.3rem;font-weight:600;color:{PALETTE['text']};margin-top:1rem;">
                    等待演讲分析数据
                </div>
                <div style="color:{PALETTE['muted']};margin-top:0.5rem;">
                    请在左侧上传演讲视频并点击「开始演讲分析」
                </div>
                <div style="color:{PALETTE['muted']};font-size:0.8rem;margin-top:0.5rem;">
                    系统将自动分析眼神交流、姿态表现、手势表达和语音表达
                </div>
            </div>
            """, unsafe_allow_html=True)

    # ============================
    # Tab 2: 过程监控
    # ============================
    with t_process:
        st.markdown(f"<div class='section-title'>流水线执行状态</div>", unsafe_allow_html=True)

        # 状态行
        _render_status_row(paths, st.session_state.running)

        st.divider()

        # 视频信息
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(f"<div class='section-title'>🎬 视频信息</div>", unsafe_allow_html=True)
            if st.session_state.video_name:
                st.markdown(f"""
                <div class="file-info">
                    <table style="width:100%;color:{PALETTE['muted']};font-size:0.82rem;">
                        <tr><td style="width:80px;">文件名</td><td style="color:{PALETTE['text']};">{st.session_state.video_name}</td></tr>
                        <tr><td>大小</td><td style="color:{PALETTE['text']};">{st.session_state.video_size_mb:.1f} MB</td></tr>
                    </table>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.caption("尚未上传视频")

        with col_b:
            st.markdown(f"<div class='section-title'>🔍 降级回退状态</div>", unsafe_allow_html=True)
            if score_data:
                warnings = score_data.get("warnings", [])
                if warnings:
                    for w in warnings:
                        st.warning(w)
                else:
                    st.success("所有分析模块均正常完成，无降级回退")
            else:
                st.caption("暂无数据")

        st.divider()

        # 日志
        st.markdown(f"<div class='section-title'>📋 实时日志</div>", unsafe_allow_html=True)
        if st.session_state.logs:
            log_text = "\n".join(st.session_state.logs[-50:])
            st.code(log_text, language="text")
        elif st.session_state.running:
            st.info("等待日志输出…")
        else:
            st.caption("暂无日志。请先开始分析。")

    # ============================
    # Tab 3: 完整报告
    # ============================
    with t_report:
        if paths["report"] is not None:
            with open(paths["report"], "r", encoding="utf-8") as f:
                report_text = f.read()
            with st.expander("📝 AI演讲反馈报告", expanded=True):
                st.markdown(report_text, unsafe_allow_html=True)
        else:
            st.info("暂无演讲反馈报告，请先完成视频分析")

    # ============================
    # Tab 4: 调试数据
    # ============================
    with t_debug:
        st.markdown(f"<div class='section-title'>最终评分 JSON</div>", unsafe_allow_html=True)
        if score_data:
            st.json(score_data)
        else:
            st.caption("暂无数据")

        st.divider()
        st.markdown(f"<div class='section-title'>原始特征文件</div>", unsafe_allow_html=True)
        raw_tab = st.selectbox("选择特征类型", ["视觉特征", "语音特征", "手势特征"], label_visibility="collapsed")
        key_map = {"视觉特征": "visual", "语音特征": "speech", "手势特征": "gesture"}
        raw_data = _load_json(paths.get(key_map[raw_tab]))
        if raw_data:
            st.json(raw_data)
        else:
            st.caption(f"{raw_tab}数据暂缺")


if __name__ == "__main__":
    main()
