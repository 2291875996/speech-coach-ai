# Streamlit → FastAPI Web 应用迁移方案

## 背景

当前 Streamlit 仪表盘存在以下问题：
- **日志不实时**：subprocess PIPE 缓冲导致过程监控为空
- **无并行分析**：视觉/语音/手势三个独立分析器串行执行，浪费 CPU
- **无任务隔离**：所有用户共享 output 目录，无法并发
- **前端轮询**：每秒 `st.rerun()` 轮询文件系统，效率低且脆弱
- **Streamlit 模型不适合长时间批处理任务**

目标：用 FastAPI + 简单前端替代，保持所有分析模块不变。

## 总体架构

```
用户浏览器 → FastAPI (async) → ThreadPoolExecutor (并行分析器)
                    ↕
               WebSocket (实时进度推送)
```

- **后端**：FastAPI + uvicorn，Jinja2 模板渲染，零构建
- **并行策略**：ThreadPoolExecutor(3) 同时跑视觉/语音/手势分析（三者独立，C 扩展释放 GIL）
- **任务管理**：UUID 隔离每个任务，JSON 文件存储索引
- **实时推送**：WebSocket 传输日志 + 进度事件
- **图表**：Plotly.js CDN 加载，前端渲染（与原版一致）

## 新增文件结构

```
server/
├── __init__.py
├── app.py              # FastAPI 应用工厂，startup 预加载 whisper 模型
├── config.py           # 服务端配置（端口、并发数、输出根目录）
├── db.py               # JSON 文件任务存储（task_index.json + 每任务 meta.json）
├── routes/
│   ├── __init__.py
│   ├── tasks.py        # REST API 端点
│   └── ws.py           # WebSocket 日志推送端点
├── services/
│   ├── __init__.py
│   ├── task_manager.py      # 任务生命周期：创建/调度/查询/取消
│   ├── pipeline_runner.py   # 核心编排：并行分析 + 评分 + 报告
│   └── log_handler.py       # queue 驱动的日志捕获器
├── templates/
│   ├── base.html       # 全局布局 + CSS + 导航
│   ├── index.html      # 上传页 + 任务列表
│   ├── task.html       # 结果页：进度动画 + 分数卡片 + 图表 + 报告
│   └── history.html    # 历史任务一览
├── static/
│   ├── css/dashboard.css    # 从 Streamlit _inject_css() 迁移
│   └── js/app.js            # 原生 JS：上传、WebSocket、Plotly 图表
run_server.py           # uvicorn 入口
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/tasks` | 上传视频 multipart → `{task_id, status}` |
| `GET` | `/api/tasks` | 所有任务列表（含分数/状态） |
| `GET` | `/api/tasks/{id}` | 单任务详情 |
| `GET` | `/api/tasks/{id}/report` | 返回 Markdown 报告 |
| `GET` | `/api/tasks/{id}/score` | 返回 score JSON |
| `DELETE` | `/api/tasks/{id}` | 删除任务 |
| `WS` | `/ws/tasks/{id}` | 实时日志 + 进度 JSON 帧 |

**WebSocket 消息格式**：
```json
{"type": "progress", "step": "visual", "status": "running"}
{"type": "progress", "step": "visual", "status": "done", "eye_contact_score": 72.5}
{"type": "log", "level": "INFO", "message": "开始视觉分析..."}
{"type": "done", "overall_score": 75.6, "grade": "良好"}
```

## 页面路由

| 路径 | 模板 | 内容 |
|------|------|------|
| `GET /` | index.html | 上传区 + 最近任务表格 |
| `GET /tasks/{id}` | task.html | 进度动画 + 得分 Hero + Plotly 图 + 报告 |
| `GET /history` | history.html | 全部历史任务（可筛选） |

## 并行执行策略

```python
# pipeline_runner.py 核心逻辑
with ThreadPoolExecutor(max_workers=3) as pool:
    futures = {
        pool.submit(visual_analyzer.analyze, video, dir): "visual",
        pool.submit(speech_analyzer.analyze, video, dir): "speech",
        pool.submit(gesture_analyzer.analyze, video, dir): "gesture",
    }
    for future in as_completed(futures):
        name = futures[future]
        results[name] = future.result()
        self._emit_progress(name, "done", ...)

# 评分和报告在全部完成后顺序执行
score = scoring_engine.compute(...)
report = report_generator.generate(...)
```

**为什么用线程而非进程**：
- 三个分析器 90% 时间在 C 扩展（OpenCV、MediaPipe、Whisper、librosa），释放 GIL
- Windows 无 `fork()`，多进程需 pickle 模型（大且不稳定）
- 最大并发任务数 2，线程总数可控（6-8）

## 日志实时推送方案

用 Python 内置 `queue.Queue` + 自定义 `logging.Handler`，替换当前的 subprocess PIPE：

1. `PipelineRunner` 创建 `queue.Queue`
2. 给 `interview_analyzer` logger 挂载 `QueueLogHandler(queue)`
3. WebSocket 端点循环从 queue 取消息，`send_json()` 推送到前端
4. 流水线结束后移除 handler

**优势**：进程内捕获，无 PIPE 缓冲问题，毫秒级延迟。

## 需要修改的现有文件

### `modules/speech_analyzer.py` — 小改动（向后兼容）

- 新增 `preload_whisper()` 函数：服务启动时预加载模型，避免每次请求加载 75MB
- `_transcribe_with_whisper()` 优先用缓存模型，加 `threading.Lock` 确保线程安全
- CLI 直接调用时行为不变（lazy load 回退）

### `requirements.txt`

新增：`fastapi`, `uvicorn[standard]`, `python-multipart`, `aiofiles`, `jinja2`, `mistune`
移除：`streamlit`（plotly、pandas 分析器未使用，可移）

### `launcher.py` — 菜单选项 [2] 改为启动 FastAPI

### 其他文件不变

`modules/visual_analyzer.py`、`gesture_analyzer.py`、`scoring_engine.py`、`report_generator.py`、`schema_validator.py`、`config/`、`models/` 全部保留不动。

## 实施顺序（6 阶段）

1. **脚手架**：创建 `server/` 包、`run_server.py`、FastAPI app 骨架、依赖安装
2. **任务管理**：REST 端点（上传/查询/删除）、JSON 文件存储、上传页面模板
3. **流水线运行器**：`pipeline_runner.py` 并行编排、日志捕获、speech_analyzer 模型缓存
4. **实时推送**：WebSocket 端点、task.html 进度动画、前端 JS WebSocket 客户端
5. **结果展示**：分数 Hero、Plotly 图表、Markdown 报告渲染、历史页面、CSS 迁移
6. **清理**：移除 streamlit 依赖、更新 launcher 菜单

## 验证方法

1. 启动 `python run_server.py`，浏览器打开 `http://localhost:8000`
2. 上传测试视频 → 确认生成 task_id → 页面自动跳转到 `/tasks/{id}`
3. 观察 WebSocket 实时日志逐行推送到页面（不再空白）
4. 确认视觉/语音/手势三个进度指示器几乎同时变为 ✅（并行生效）
5. 分析完成后，确认得分 Hero、雷达图、柱状图、报告均正确渲染
6. 上传第二个视频，确认两个任务互不干扰
7. `python main.py video.mp4` CLI 模式仍可独立使用
