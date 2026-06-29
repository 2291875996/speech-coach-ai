# 🎤 AI 演讲反馈教练 (AI Speech Coach)

基于多模态人工智能的演讲分析系统。上传演讲视频，自动分析**眼神交流、姿态表现、手势表达、语音表达**四个维度，生成综合评分和中文反馈报告。

## 快速开始

```bash
# Web 前端
双击 启动Web.bat → 浏览器打开 http://localhost:8000

# 命令行模式
python main.py your_speech.mp4 -o ./output
```

## 分析维度

| 维度 | 技术 | 指标 |
|------|------|------|
| 👁️ 眼神交流 | MediaPipe Face Landmarker (478点) + EAR | 眼神接触比例、眨眼率 |
| 🧍 姿态表现 | solvePnP + 几何回退 | 头部 pitch/yaw/roll、稳定性 |
| 🤝 手势表达 | MediaPipe Hand Landmarker (21点) | 手势频率、种类、运动幅度 |
| 🎤 语音表达 | Whisper tiny + librosa pyin | 语速、音高变化、填充词统计 |

## 技术架构

```
FastAPI + WebSocket + ThreadPoolExecutor
    ├── visual_analyzer  (MediaPipe, CPU, ~50s)
    ├── speech_analyzer   (Whisper, CPU, ~3s 缓存后)
    └── gesture_analyzer  (MediaPipe, CPU, ~55s)
            ↓ 三分析器并行
    scoring_engine (加权求和)
            ↓
    calibration (可信度验证)
            ↓
    report_generator (中文 Markdown 报告)
```

- **后端**: FastAPI + uvicorn, Jinja2 模板, 零前端构建
- **实时推送**: WebSocket + EventEmitter (结构化事件流)
- **任务管理**: UUID 隔离, ThreadPoolExecutor 并发控制
- **可信度**: 每维度 confidence 评分 + bias_estimate + 误差来源追溯

## 项目结构

```
interview_analyzer/
├── main.py              # CLI 入口 (python main.py video.mp4)
├── run_server.py        # Web 入口
├── 启动Web.bat           # 一键启动
├── modules/
│   ├── visual_analyzer.py    # 视觉分析
│   ├── speech_analyzer.py    # 语音分析 (Whisper 缓存)
│   ├── gesture_analyzer.py   # 手势分析
│   ├── scoring_engine.py     # 综合评分
│   ├── report_generator.py   # 报告生成
│   ├── calibration.py        # 可信度验证
│   └── schema_validator.py   # 输出校验
├── server/
│   ├── app.py                # FastAPI 应用
│   ├── routes/tasks.py       # REST API
│   ├── routes/ws.py          # WebSocket
│   ├── services/
│   │   ├── pipeline_runner.py  # 并行流水线
│   │   ├── event_system.py     # 事件总线
│   │   └── task_manager.py     # 任务管理
│   ├── templates/            # Jinja2 页面
│   └── static/               # CSS + JS
├── config/                   # 权重/阈值配置
├── models/                   # MediaPipe 模型文件
└── output/tasks/<uuid>/      # 每任务独立输出
    ├── features/   (visual/speech/gesture JSON)
    ├── reports/    (final_score + report + confidence)
    └── logs/       (events.jsonl)
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/tasks` | 上传视频 |
| `GET` | `/api/tasks` | 任务列表 |
| `GET` | `/api/tasks/{id}` | 任务详情 |
| `GET` | `/api/tasks/{id}/score` | 评分 JSON |
| `GET` | `/api/tasks/{id}/report` | Markdown 报告 |
| `GET` | `/api/tasks/{id}/confidence` | 可信度报告 |
| `GET` | `/api/version` | 版本信息 |
| `WS` | `/ws/tasks/{id}` | 实时事件流 |

## 核心设计

- **CLI + Web 双入口**: `python main.py` 命令行和 Web 共用同一套分析管线
- **事件驱动**: EventEmitter → Queue → WebSocket → 前端 progressive reducer
- **并行分析**: 视觉/语音/手势三分析器 ThreadPoolExecutor 并行
- **可信度透明**: 每个分数可追溯到分析方法、检测率、偏差估计、误差来源
- **协议锁定**: BUILD_ID 缓存爆破 + PROTOCOL_VERSION 握手 + NoCacheMiddleware

## 已知局限

参见 `confidence_report.json`，主要误差来源：
- 相机焦距估算偏差导致姿态 pitch 系统性偏移 (~±5°)
- 手势按帧累计计数（非独立手势去重）
- Whisper tiny 中文准确率约 85-90%
