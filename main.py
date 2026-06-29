"""
AI 演讲反馈教练 入口模块
编排完整的演讲分析流水线：视觉 → 语音 → 手势 → 评分 → 报告
仅CPU推理，离线批处理模式。
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# 确保项目根目录在 sys.path 中，支持从任意目录运行
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from modules import visual_analyzer, speech_analyzer, gesture_analyzer
from modules import scoring_engine, report_generator, schema_validator

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logger = logging.getLogger("interview_analyzer")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(
    logging.Formatter("[%(levelname)s] %(asctime)s - %(name)s: %(message)s",
                      datefmt="%H:%M:%S")
)
logger.addHandler(_handler)


def _print_banner():
    """打印启动横幅。"""
    print("""
╔══════════════════════════════════════════════════╗
║       AI演讲反馈教练 (AI Speech Coach)             ║
║  基于多模态人工智能的演讲分析系统                    ║
║  CPU推理 | 离线批处理 | 中文报告                    ║
╚══════════════════════════════════════════════════╝
    """)


def run_pipeline(
    video_path: str,
    output_dir: Optional[str] = None,
    skip_visual: bool = False,
    skip_speech: bool = False,
    skip_gesture: bool = False,
    skip_report: bool = False,
) -> int:
    """执行完整的演讲分析流水线。

    Args:
        video_path: 输入演讲视频文件路径
        output_dir: 输出根目录，默认项目下的 output/
        skip_visual: 跳过视觉分析
        skip_speech: 跳过语音分析
        skip_gesture: 跳过手势分析
        skip_report: 跳过报告生成

    Returns:
        0 表示成功，1 表示失败
    """
    if not os.path.isfile(video_path):
        logger.error("视频文件不存在: %s", video_path)
        return 1

    # 确定输出目录
    if output_dir is None:
        output_dir = str(_PROJECT_ROOT / "output")
    features_dir = os.path.join(output_dir, "features")
    reports_dir = os.path.join(output_dir, "reports")

    os.makedirs(features_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)

    logger.info("输出目录: %s", output_dir)
    logger.info("视频文件: %s", video_path)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("演讲分析流水线启动时间: %s", timestamp)

    visual_path: Optional[str] = None
    speech_path: Optional[str] = None
    gesture_path: Optional[str] = None
    errors: list = []

    # ---- Step 1: 视觉分析（眼神交流 + 头部姿态） ----
    if not skip_visual:
        logger.info("=" * 50)
        logger.info("步骤 1/5: 视觉分析（眼神交流 + 头部姿态）")
        logger.info("=" * 50)
        try:
            vf = visual_analyzer.analyze(video_path, output_dir=features_dir)
            source_name = Path(video_path).stem
            visual_path = os.path.join(
                features_dir, f"visual_features_{source_name}.json"
            )
            logger.info("视觉分析完成: 眼神交流=%.1f, 姿态=%.1f",
                         vf["dimension_scores"]["eye_contact_score"],
                         vf["dimension_scores"]["posture_score"])
            # CHECKPOINT_1: 校验视觉输出（WARNING级别，不终止）
            if not schema_validator.validate_visual_output(vf):
                logger.warning("CHECKPOINT_1 降级: 视觉输出校验失败，继续执行")
        except Exception as exc:
            logger.error("视觉分析步骤失败: %s", exc)
            errors.append(f"视觉分析: {exc}")
    else:
        logger.info("步骤 1/5: 视觉分析 -- 已跳过")

    # ---- Step 2: 语音分析 ----
    if not skip_speech:
        logger.info("=" * 50)
        logger.info("步骤 2/5: 语音分析（转录 + 语速 + 音高 + 填充词）")
        logger.info("=" * 50)
        try:
            sf = speech_analyzer.analyze(video_path, output_dir=features_dir)
            source_name = Path(video_path).stem
            speech_path = os.path.join(
                features_dir, f"speech_features_{source_name}.json"
            )
            logger.info("语音分析完成: 语速=%.1f wpm, 得分=%.1f",
                         sf["speech_rate_features"]["words_per_minute"],
                         sf["dimension_scores"]["speech_score"])
            # CHECKPOINT_2: 校验语音输出（WARNING级别，不终止）
            if not schema_validator.validate_speech_output(sf):
                logger.warning("CHECKPOINT_2 降级: 语音输出校验失败，继续执行")
        except Exception as exc:
            logger.error("语音分析步骤失败: %s", exc)
            errors.append(f"语音分析: {exc}")
    else:
        logger.info("步骤 2/5: 语音分析 -- 已跳过")

    # ---- Step 3: 手势分析 ----
    if not skip_gesture:
        logger.info("=" * 50)
        logger.info("步骤 3/5: 手势分析（手部关键点检测 + 规则分类）")
        logger.info("=" * 50)
        try:
            gf = gesture_analyzer.analyze(video_path, output_dir=features_dir)
            source_name = Path(video_path).stem
            gesture_path = os.path.join(
                features_dir, f"gesture_features_{source_name}.json"
            )
            logger.info("手势分析完成: 手势/分钟=%.1f, 种类=%d, 得分=%.1f",
                         gf["gesture_statistics"]["gestures_per_minute"],
                         gf["gesture_statistics"]["gesture_variety_count"],
                         gf["dimension_scores"]["gesture_score"])
            # CHECKPOINT_3: 校验手势输出（WARNING级别，不终止）
            if not schema_validator.validate_gesture_output(gf):
                logger.warning("CHECKPOINT_3 降级: 手势输出校验失败，继续执行")
        except Exception as exc:
            logger.error("手势分析步骤失败: %s", exc)
            errors.append(f"手势分析: {exc}")
    else:
        logger.info("步骤 3/5: 手势分析 -- 已跳过")

    # ---- Step 4: 汇总校验 ----
    logger.info("=" * 50)
    logger.info("步骤 4/6: 汇总 Schema 校验")
    logger.info("=" * 50)

    if visual_path is None:
        visual_path = ""
    if speech_path is None:
        speech_path = ""
    if gesture_path is None:
        gesture_path = ""

    if not schema_validator.validate_all_features(visual_path, speech_path, gesture_path):
        logger.warning("汇总校验降级: 部分特征文件校验未通过，继续执行评分")

    # ---- Step 5: 综合评分 ----
    logger.info("=" * 50)
    logger.info("步骤 5/6: 综合评分计算")
    logger.info("=" * 50)

    try:
        score_data = scoring_engine.compute(
            visual_features_path=visual_path,
            speech_features_path=speech_path,
            gesture_features_path=gesture_path,
            output_dir=reports_dir,
        )
        logger.info("综合评分: %.1f 分, 等级: %s",
                     score_data["overall_score"], score_data["grade"])

        # CHECKPOINT_4: 校验评分输出（ERROR级别，唯一终止点）
        if not schema_validator.validate_score_output(score_data):
            logger.error("CHECKPOINT_4 失败: 评分输出校验未通过，流水线终止")
            return 1

        # 定位评分文件路径
        score_files = sorted(
            [f for f in os.listdir(reports_dir) if f.startswith("final_score_")],
            reverse=True,
        )
        score_path = os.path.join(reports_dir, score_files[0]) if score_files else ""

        if score_data.get("warnings"):
            logger.warning("评分过程中存在 %d 条警告", len(score_data["warnings"]))
            for w in score_data["warnings"]:
                logger.warning("  - %s", w)
    except Exception as exc:
        logger.error("综合评分步骤失败: %s", exc)
        errors.append(f"综合评分: {exc}")
        score_path = ""

    # ---- Step 6: 报告生成 ----
    if not skip_report:
        logger.info("=" * 50)
        logger.info("步骤 6/6: 生成中文分析报告")
        logger.info("=" * 50)
        try:
            report = report_generator.generate(
                score_json_path=score_path,
                visual_features_path=visual_path,
                speech_features_path=speech_path,
                gesture_features_path=gesture_path,
                output_dir=reports_dir,
            )
            logger.info("中文分析报告已生成")
        except Exception as exc:
            logger.error("报告生成步骤失败: %s", exc)
            errors.append(f"报告生成: {exc}")
    else:
        logger.info("步骤 5/5: 报告生成 -- 已跳过")

    # ---- 结果摘要 ----
    print()
    logger.info("=" * 50)
    logger.info("流水线执行完毕")
    logger.info("=" * 50)

    if score_path and os.path.isfile(score_path):
        with open(score_path, "r", encoding="utf-8") as f:
            import json
            final = json.load(f)
        print(f"  综合得分: {final['overall_score']:.1f} / 100")
        print(f"  评分等级: {final['grade']}")
        print(f"  输出目录: {output_dir}")

    if errors:
        print(f"  错误数: {len(errors)}")
        for e in errors:
            print(f"    - {e}")
        return 1

    return 0


def _validate_environment() -> bool:
    """验证运行环境依赖是否就绪。"""
    issues = []

    # 检查 Python 版本
    py_version = sys.version_info
    if py_version < (3, 10):
        issues.append(f"Python 版本过低: {py_version.major}.{py_version.minor}，需要 >= 3.10")

    # 检查核心依赖
    for module_name, pip_name in [
        ("cv2", "opencv-python"),
        ("mediapipe", "mediapipe"),
        ("numpy", "numpy"),
        ("yaml", "pyyaml"),
    ]:
        try:
            __import__(module_name)
        except ImportError:
            issues.append(f"缺少依赖: {pip_name}")

    if issues:
        logger.error("环境验证失败:")
        for issue in issues:
            logger.error("  - %s", issue)
        logger.error("请运行: pip install -r requirements.txt")
        return False

    logger.info("环境验证通过")
    return True


class InterviewAnalyzerPipeline:
    """演讲分析流水线类，按顺序调用所有分析模块。

    每步保存中间 JSON 文件，输出最终评分 JSON，生成中文演讲反馈报告。
    """

    def __init__(
        self,
        video_path: str,
        output_dir: Optional[str] = None,
        skip_visual: bool = False,
        skip_speech: bool = False,
        skip_gesture: bool = False,
        skip_report: bool = False,
    ):
        self.video_path = video_path
        self.output_dir = output_dir
        self.skip_visual = skip_visual
        self.skip_speech = skip_speech
        self.skip_gesture = skip_gesture
        self.skip_report = skip_report

    def run(self) -> int:
        """执行完整演讲分析流水线：视觉 → 语音 → 手势 → 评分 → 报告。"""
        exit_code = run_pipeline(
            video_path=self.video_path,
            output_dir=self.output_dir,
            skip_visual=self.skip_visual,
            skip_speech=self.skip_speech,
            skip_gesture=self.skip_gesture,
            skip_report=self.skip_report,
        )
        if exit_code == 0:
            logger.info("演讲分析完成")
        return exit_code


def main():
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description="AI演讲反馈教练 - 自动分析演讲视频中的视觉、语音和手势表现",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py speech.mp4
  python main.py speech.mp4 -o ./my_output
  python main.py speech.mp4 --skip-gesture
  python main.py speech.mp4 -o ./output --skip-visual --skip-speech
        """,
    )
    parser.add_argument("video", help="输入视频文件路径")
    parser.add_argument("-o", "--output-dir", default=None,
                        help="输出目录路径（默认: output/）")
    parser.add_argument("--skip-visual", action="store_true",
                        help="跳过视觉分析步骤")
    parser.add_argument("--skip-speech", action="store_true",
                        help="跳过语音分析步骤")
    parser.add_argument("--skip-gesture", action="store_true",
                        help="跳过手势分析步骤")
    parser.add_argument("--skip-report", action="store_true",
                        help="跳过报告生成步骤")
    parser.add_argument("--validate", action="store_true",
                        help="仅验证环境依赖，不执行分析")

    args = parser.parse_args()

    _print_banner()

    # 环境验证
    if not _validate_environment():
        if args.validate:
            sys.exit(1)
        logger.warning("环境存在缺失依赖，部分功能可能不可用")
        logger.warning("请运行: pip install -r requirements.txt")
    else:
        if args.validate:
            logger.info("所有依赖已就绪")
            sys.exit(0)

    # 执行流水线
    pipeline = InterviewAnalyzerPipeline(
        video_path=args.video,
        output_dir=args.output_dir,
        skip_visual=args.skip_visual,
        skip_speech=args.skip_speech,
        skip_gesture=args.skip_gesture,
        skip_report=args.skip_report,
    )
    exit_code = pipeline.run()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
