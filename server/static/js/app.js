/**
 * AI 演讲反馈教练 — 前端客户端 (Phase 4)
 * 上传 / WebSocket进度 / Plotly图表 / 报告渲染 / 历史记录
 */
(function(){'use strict';

// ═══════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════

function fmtSec(s) {
    if (s < 60) return s.toFixed(1) + 's';
    var m = Math.floor(s / 60);
    var sec = Math.floor(s % 60);
    return m + 'm ' + sec + 's';
}

function fmtSize(b) { return (b/1024/1024).toFixed(1) + ' MB'; }
function fmtDate(s) { try { return new Date(s).toLocaleString('zh-CN'); } catch(e) { return s; } }

function getLevel(score) {
    if (score >= 85) return {label:'优秀', color:'#059669', emoji:'⭐'};
    if (score >= 70) return {label:'良好', color:'#2563EB', emoji:'✅'};
    if (score >= 50) return {label:'一般', color:'#D97706', emoji:'⚠️'};
    return {label:'需改进', color:'#DC2626', emoji:'📉'};
}

function statusBadge(status) {
    // 优先复用 statusLabel() 的完整状态映射，避免重复维护
    var label = statusLabel(status);
    // CSS 类映射：仅 4 种视觉样式（ok/running/failed/waiting）
    var cls;
    if (status === 'completed') cls = 'ok';
    else if (status === 'failed') cls = 'failed';
    else if (status === 'pending' || status === 'uploaded') cls = 'waiting';
    else cls = 'running';  // visual_running, speech_running, gesture_running, scoring, reporting
    return '<span class="badge ' + cls + '">' + label + '</span>';
}

// ═══════════════════════════════════════════════════════════════════════════
// 简单 Markdown → HTML
// ═══════════════════════════════════════════════════════════════════════════
function mdToHtml(md) {
    return md
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
        .replace(/^### (.+)$/gm,'<h3>$1</h3>')
        .replace(/^## (.+)$/gm,'<h2>$1</h2>')
        .replace(/^# (.+)$/gm,'<h1>$1</h1>')
        .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
        .replace(/\*(.+?)\*/g,'<em>$1</em>')
        .replace(/`([^`]+)`/g,'<code>$1</code>')
        .replace(/^- (.+)$/gm,'<li>$1</li>')
        .replace(/(<li>.*<\/li>\n?)+/g,'<ul>$&</ul>')
        .replace(/\n/g,'<br>');
}

// ═══════════════════════════════════════════════════════════════════════════
// 画 Plotly 图表
// ═══════════════════════════════════════════════════════════════════════════

var DIM_ORDER = ['eye_contact_score','posture_score','gesture_score','speech_score'];
var DIM_LABELS = {
    eye_contact_score: {icon:'👁️', name:'眼神交流'},
    posture_score:      {icon:'🧍', name:'姿态表现'},
    gesture_score:      {icon:'🤝', name:'手势表达'},
    speech_score:       {icon:'🎤', name:'语音表达'}
};
var DIM_COLORS = ['#3B82F6','#10B981','#8B5CF6','#06B6D4'];

function plotRadar(divId, dims) {
    var labels = DIM_ORDER.map(function(k){return DIM_LABELS[k].name;});
    var values = DIM_ORDER.map(function(k){return dims[k]||0;});
    values.push(values[0]);
    var data = [{
        type:'scatterpolar', r:values, theta:labels.concat([labels[0]]),
        fill:'toself', fillcolor:'rgba(59,130,246,0.15)',
        line:{color:'#3B82F6',width:2.5},
        marker:{size:7,color:'#3B82F6'}
    }];
    var layout = {
        polar:{radialaxis:{range:[0,100],tickfont:{color:'#64748B',size:10},gridcolor:'rgba(0,0,0,0.06)'},
               angularaxis:{tickfont:{color:'#1E293B',size:12},gridcolor:'rgba(0,0,0,0.06)'},
               bgcolor:'rgba(0,0,0,0)'},
        paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
        margin:{l:40,r:40,t:30,b:30}, height:350, showlegend:false
    };
    Plotly.newPlot(divId, data, layout, {displayModeBar:false, responsive:true});
}

function plotBar(divId, dims) {
    var labels = DIM_ORDER.map(function(k){return DIM_LABELS[k].name;});
    var values = DIM_ORDER.map(function(k){return dims[k]||0;});
    var data = [{
        type:'bar', x:labels, y:values,
        marker:{color:DIM_COLORS, line:{color:'rgba(255,255,255,0.08)',width:1}},
        text:values.map(function(v){return v.toFixed(1);}), textposition:'outside',
        textfont:{color:'#1E293B',size:14}
    }];
    var layout = {
        yaxis:{range:[0,110],tickfont:{color:'#64748B'},gridcolor:'rgba(0,0,0,0.05)'},
        xaxis:{tickfont:{color:'#1E293B',size:12}},
        paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
        margin:{l:20,r:20,t:30,b:20}, height:350, showlegend:false
    };
    [85,70,50].forEach(function(y){
        layout.shapes = layout.shapes || [];
        layout.shapes.push({type:'line',x0:-0.5,x1:3.5,y0:y,y1:y,line:{dash:'dash',color:'#94A3B8',width:1}});
    });
    Plotly.newPlot(divId, data, layout, {displayModeBar:false, responsive:true});
}

function plotWeights(divId, weights) {
    var labels = DIM_ORDER.map(function(k){return DIM_LABELS[k].name;});
    // weights_used 键名无 _score 后缀（Schema: eye_contact/posture/gesture/speech）
    // 使用 replace 将 DIM_ORDER 的 _score 键映射到 weights_used 键
    // 兼容性：后端新增 _score 后缀维度时，只需更新 DIM_ORDER，replace 自动适配
    // 注意：此映射仅影响 Plotly 饼图，Radar/Bar/Report 等逻辑不经过此路径
    var values = DIM_ORDER.map(function(k){
        var wk = k.replace('_score','');
        var v = weights[wk];
        return (typeof v === 'number' && v > 0) ? v : 0;
    });
    var data = [{
        type:'pie', labels:labels, values:values,
        marker:{colors:DIM_COLORS, line:{color:'rgba(0,0,0,0.4)',width:1}},
        textinfo:'label+percent', textfont:{color:'#1E293B',size:11}, hole:0.55
    }];
    var layout = {paper_bgcolor:'rgba(0,0,0,0)', margin:{l:10,r:10,t:10,b:10}, height:350, showlegend:false};
    Plotly.newPlot(divId, data, layout, {displayModeBar:false, responsive:true});
}

// ═══════════════════════════════════════════════════════════════════════════
// 表格自动刷新 — 所有页面通用（首页 / 历史页 / 任务详情页）
// ═══════════════════════════════════════════════════════════════════════════

function loadTaskList(tbody) {
    fetch('/api/tasks?limit=20').then(function(r){return r.json();}).then(function(tasks){
        if (!tasks.length) { tbody.innerHTML = '<tr><td colspan="6" class="empty-row">暂无分析任务</td></tr>'; return; }
        tbody.innerHTML = tasks.map(function(t){
            var s = t.status, sc = t.overall_score != null ? t.overall_score.toFixed(1) : '—';
            return '<tr><td>'+(t.filename||'—')+'</td><td>'+statusBadge(s)+'</td><td>'+sc+'</td><td>'+(t.grade||'—')+'</td><td>'+fmtDate(t.created_at)+'</td><td>'+(s==='completed'?'<a class="btn-sm" href="/tasks/'+t.id+'">查看</a>':'—')+'</td></tr>';
        }).join('');
    }).catch(function(e){ tbody.innerHTML = '<tr><td colspan="6" class="empty-row">加载失败</td></tr>'; });
}

// 公共：首次加载 + 5 秒自动刷新，首页与历史页复用
function autoRefreshTable(tbody) {
    if (!tbody) return;
    loadTaskList(tbody);
    setInterval(function(){ loadTaskList(tbody); }, 5000);
}

// ═══════════════════════════════════════════════════════════════════════════
// 上传页专属
// ═══════════════════════════════════════════════════════════════════════════
var fileInput = document.getElementById('file-input');
if (fileInput) {
    var uploadZone = document.getElementById('upload-zone');
    var fileInfo = document.getElementById('file-info');
    var fileName = document.getElementById('file-name');
    var fileSize = document.getElementById('file-size');
    var btnAnalyze = document.getElementById('btn-analyze');
    var statusText = document.getElementById('status-text');
    var progressBar = document.getElementById('progress-bar');
    var progressFill = document.getElementById('progress-fill');
    var selectedFile = null;

    fileInput.addEventListener('change', function(){
        selectedFile = this.files[0];
        if (selectedFile) {
            fileName.textContent = '📄 ' + selectedFile.name;
            fileSize.textContent = '📦 ' + fmtSize(selectedFile.size);
            fileInfo.style.display = 'block';
            btnAnalyze.disabled = false;
        }
    });

    if (uploadZone) {
        uploadZone.addEventListener('dragover', function(e){ e.preventDefault(); uploadZone.classList.add('drag-over'); });
        uploadZone.addEventListener('dragleave', function(){ uploadZone.classList.remove('drag-over'); });
        uploadZone.addEventListener('drop', function(e){
            e.preventDefault(); uploadZone.classList.remove('drag-over');
            if (e.dataTransfer.files.length > 0) {
                fileInput.files = e.dataTransfer.files;
                fileInput.dispatchEvent(new Event('change'));
            }
        });
    }

    window.startAnalysis = async function(){
        if (!selectedFile) return;
        btnAnalyze.disabled = true; btnAnalyze.textContent = '⏳ 上传中...';
        progressBar.style.display = 'block'; progressFill.style.width = '30%';
        statusText.textContent = '正在上传视频...';
        var fd = new FormData(); fd.append('file', selectedFile);
        try {
            var resp = await fetch('/api/tasks', {method:'POST', body:fd});
            if (!resp.ok) { var err = await resp.json(); throw new Error(err.detail||'上传失败'); }
            var data = await resp.json();
            progressFill.style.width = '100%';
            statusText.textContent = '✅ 已创建，正在跳转...';
            setTimeout(function(){ window.location.href = '/tasks/' + data.task_id; }, 500);
        } catch(e) {
            statusText.textContent = '❌ ' + e.message;
            btnAnalyze.disabled = false; btnAnalyze.textContent = '🚀 开始演讲分析';
            progressBar.style.display = 'none'; progressFill.style.width = '0%';
        }
    };
}

// ── 所有页面的表格初始化（null-safe，不存在的 tbody 自动跳过）──
autoRefreshTable(document.getElementById('task-tbody'));
autoRefreshTable(document.getElementById('history-tbody'));

// ═══════════════════════════════════════════════════════════════════════════
// 任务详情页 (WebSocket)
// ═══════════════════════════════════════════════════════════════════════════
if (typeof TASK_ID !== 'undefined') { initTaskPage(TASK_ID); }

function initTaskPage(taskId) {
    var container = document.getElementById('task-container');
    if (!container) return;  // 防御：非任务详情页不初始化
    var dims = {};
    var rendered = false;
    var stepStates = {visual:'waiting',speech:'waiting',gesture:'waiting',scoring:'waiting',report:'waiting'};
    window._stageDisplayTimer = {};  // 空对象 = 无延迟，data done → 视觉立即 done

    // ═══════════════════════════════════════════════════════════════
    // 0. UI 控制状态机 — 可控发布 + 可预测渲染
    // ═══════════════════════════════════════════════════════════════
    window.uiState = {
        phase: 'running',       // idle | running | locked | done
        lockRender: false,      // true 时禁止所有 UI 更新
        stepBarrier: null,      // 非 null 时只允许该 step 的事件通过
        lastEventSeq: 0         // 最后处理的事件序号
    };
    var barrierQueue = [];      // 被 barrier 拦截的事件缓存
    var BARRIER_TIMEOUT = 5000; // barrier 超时自动释放 (ms)
    var BARRIER_QUEUE_MAX = 100; // barrierQueue 上限，防止内存无限增长
    var barrierTimer = null;    // barrier 超时定时器 ID

    function _startBarrierTimer(stage) {
        _clearBarrierTimer();
        barrierTimer = setTimeout(function(){
            console.warn('[BARRIER] 超时未释放 stage=' + stage + '，自动释放并排空 ' + barrierQueue.length + ' 条缓存事件');
            window.uiState.stepBarrier = null;
            _drainBarrierQueue();
            // 仅当 drain 未重新建立 barrier 时才清空 timer 引用
            // 若 drain 中触发了新的 STEP_START，_startBarrierTimer 已覆写 barrierTimer
            if (window.uiState.stepBarrier === null) {
                barrierTimer = null;
            }
        }, BARRIER_TIMEOUT);
    }

    function _clearBarrierTimer() {
        if (barrierTimer) {
            clearTimeout(barrierTimer);
            barrierTimer = null;
        }
    }

    // ═══════════════════════════════════════════════════════════════
    // 跨通道状态缓存 — WS ↔ HTTP 数据融合
    // ═══════════════════════════════════════════════════════════════
    window.runtimeState = {
        dims: dims,
        stepStates: stepStates,
        stepCostMap: stepCostMap,
        overall: null,
        grade: null,
        timing: null,
        rendered: false,
        taskId: taskId
    };

    container.innerHTML = '<p class="loading-text" id="status-text"><span class="spinner"></span>正在连接...</p>' +
        buildStepList({visual:'waiting',speech:'waiting',gesture:'waiting',scoring:'waiting',report:'waiting'});

    // ═══════════════════════════════════════════════════════════════
    // 1. Event Timeline Buffer — 存储所有事件，支持渐进式回放
    // ═══════════════════════════════════════════════════════════════
    var eventBuffer = [];
    var bufferTimer = null;
    var STAGE_DELAY = 400; // 阶段间动画延迟 (ms)

    // ═══════════════════════════════════════════════════════════════
    // 步骤最小视觉驻留 — 单一定时器模型，不引入额外状态机
    //
    // 原理: stepStates 是唯一真相源 (waiting→running→done)
    //       STEP_END 立即 setStepDone（barrier 释放 + stepStates 更新）
    //       window._stageDisplayTimer[stage] 控制视觉 done 展示时机
    //       rebuildStepList 检查 timer 是否活跃 → 显示 "即将完成" vs "✓"
    // ═══════════════════════════════════════════════════════════════

    // ── STEP_END: data done = 立即 ✓（无延迟）──
    function _onStepEndVisual(stage) {
        safeRender(function(){
            rebuildStepList(stepStates);
            updateStatusText(statusFromSteps(stepStates));
        });
    }

    // ── DONE: 全部 ✓ → 清理残留 → 600ms → loadFinalResults ──
    function _finalizeVisualPipeline(overall, grade) {
        console.log('[VISUAL] finalize');
        rendered = true;
        window.runtimeState.rendered = true;
        // 统一清理所有未触发的 display timer + drain 调度
        Object.keys(window._stageDisplayTimer).forEach(function(k){
            clearTimeout(window._stageDisplayTimer[k]);
            delete window._stageDisplayTimer[k];
        });
        if (typeof _drainScheduled !== 'undefined') _drainScheduled = false;
        safeRender(function(){
            rebuildStepList(stepStates);
            updateStatusText(statusFromSteps(stepStates));
        });
        updateStatusText('✅ 分析完成，加载报告...');
        setTimeout(function(){
            window.uiState.phase = 'locked';
            _drainBarrierQueue();
            window.uiState.phase = 'done';
            loadFinalResults(taskId, {overall_score: overall, grade: grade}, dims);
        }, 600);
    }

    // ── 紧急排空（WS 断连）──
    function _abortVisualPipeline() {
        safeRender(function(){
            rebuildStepList(stepStates);
            updateStatusText(statusFromSteps(stepStates));
        });
    }

    var NORMALIZE = {
        STAGE_START: 'STEP_START', STAGE_END: 'STEP_END',
        PROGRESS: 'STEP_PROGRESS',
        PIPELINE_DONE: 'DONE', METRIC: 'METRIC', LOG: 'LOG', ERROR: 'ERROR',
        STATUS: 'STATUS', HEARTBEAT: 'HEARTBEAT', STREAM_END: 'STREAM_END',
        SYSTEM_INIT: 'SYSTEM_INIT'
    };

    // ── safeRender: requestAnimationFrame 节流，lock 时拒绝 ──
    function safeRender(fn) {
        if (window.uiState.lockRender) return;
        requestAnimationFrame(fn);
    }

    // ── barrier 检查: 决定事件是立即处理还是缓存 ──
    function _passesBarrier(evt) {
        var ut = window.uiState;
        if (ut.phase !== 'running') {
            console.log('[BARRIER-CHECK] BLOCKED ' + evt.event_type + ' ' + (evt.stage||'') + ' reason=phase=' + ut.phase);
            return false;
        }
        if (ut.lockRender) {
            console.log('[BARRIER-CHECK] BLOCKED ' + evt.event_type + ' ' + (evt.stage||'') + ' reason=lockRender');
            return false;
        }
        // 系统事件 + DONE 始终放行，不受 barrier 限制
        var sysTypes = ['SYSTEM_INIT','LOG','ERROR','HEARTBEAT','STREAM_END','STATUS','DONE'];
        if (sysTypes.indexOf(evt.event_type) >= 0) return true;
        if (ut.stepBarrier !== null && evt.stage !== ut.stepBarrier) {
            console.log('[BARRIER-CHECK] BLOCKED ' + evt.event_type + ' ' + evt.stage + ' reason=barrier=' + ut.stepBarrier + ' queue=' + barrierQueue.length);
            return false;
        }
        return true;
    }

    // ── 异步逐步释放 barrierQueue：每次只处理 1 条，给 UI 留出渲染帧 ──
    var _drainScheduled = false;
    var DRAIN_INTERVAL = 32; // ~30fps，每帧约 32ms，确保 rebuildStepList 有 rAF 窗口

    function _drainBarrierQueue() {
        // 去重：避免多次调用导致并行 drain
        if (_drainScheduled) return;
        _drainScheduled = true;

        function _drainOne() {
            // 溢出保护
            if (barrierQueue.length > BARRIER_QUEUE_MAX) {
                var overflow = barrierQueue.length - BARRIER_QUEUE_MAX;
                console.warn('[BARRIER] 队列溢出，丢弃最早 ' + overflow + ' 条事件 (当前 ' + barrierQueue.length + ')');
                barrierQueue = barrierQueue.slice(-BARRIER_QUEUE_MAX);
            }
            if (barrierQueue.length === 0) {
                _drainScheduled = false;
                return;
            }
            // 找第一个可通过 barrier 的事件
            for (var i = 0; i < barrierQueue.length; i++) {
                var evt = barrierQueue[i];
                if (_passesBarrier(evt)) {
                    // 从队列中移除该事件
                    barrierQueue.splice(i, 1);
                    console.log('[DRAIN] pop: ' + evt.event_type + ' ' + (evt.stage||'') + ' barrier=' + window.uiState.stepBarrier + ' remaining=' + barrierQueue.length);
                    reduce(evt);
                    console.log('[DRAIN] after reduce: barrier=' + window.uiState.stepBarrier + ' stepStates=' + JSON.stringify(stepStates));
                    // 调度下一条，给 UI 一帧时间渲染
                    setTimeout(_drainOne, DRAIN_INTERVAL);
                    return;
                }
            }
            // 无事件可通过 barrier → 停止 drain
            _drainScheduled = false;
        }

        _drainOne();
    }

    // ── SEQ 检查 + Barrier 过滤的入口 ──
    function processEvent(evt) {
        var ut = window.uiState;
        // SEQ 检查: 丢弃乱序/重复事件
        var seq = evt.seq || 0;
        if (seq > 0 && seq <= ut.lastEventSeq) {
            console.warn('[SEQ] 丢弃过期事件 seq=' + seq + ' last=' + ut.lastEventSeq);
            return;
        }
        if (seq > 0) ut.lastEventSeq = seq;

        // Barrier 检查
        if (!_passesBarrier(evt)) {
            barrierQueue.push(evt);
            // [BARRIER] 诊断：记录被拦截的非系统事件
            var sysTypes = ['SYSTEM_INIT','LOG','ERROR','HEARTBEAT','STREAM_END','STATUS'];
            if (sysTypes.indexOf(evt.event_type) < 0) {
                console.log('[BARRIER] blocked', evt.event_type, evt.stage, 'barrier=' + ut.stepBarrier, 'q=' + barrierQueue.length);
            }
            return;
        }

        reduce(evt);
    }

    // ── 步骤状态原子操作 ──
    var stepCostMap = {};       // {stage: cost_seconds}

    function setStepRunning(stage) {
        var old = stepStates[stage];
        if (stepStates[stage] !== 'running') {
            stepStates[stage] = 'running';
            // [CP4] STATE transition
            console.log('[STATE] ' + stage + ': ' + old + ' → running  stepStates=' + JSON.stringify(stepStates));
            // 设置 barrier，确保当前步骤优先展示
            if (window.uiState.stepBarrier === null) {
                window.uiState.stepBarrier = stage;
                _startBarrierTimer(stage);  // 启动超时保护
            }
            return true;
        }
        return false;
    }

    function setStepDone(stage, status) {
        var old = stepStates[stage];
        var newState = (status === 'failed') ? 'error' : 'done';
        if (stepStates[stage] !== newState) {
            stepStates[stage] = newState;
            // [CP4] STATE transition
            console.log('[STATE] ' + stage + ': ' + old + ' → ' + newState + ' (barrier=' + window.uiState.stepBarrier + ')');
            // 释放 barrier，允许下一个步骤通过
            if (window.uiState.stepBarrier === stage) {
                window.uiState.stepBarrier = null;
                _clearBarrierTimer();  // 正常释放，取消超时定时器
            }
            return true;
        }
        return false;
    }

    function setStepProgress(stage, percent) {
        var labelEl = document.getElementById('label-' + stage);
        if (labelEl && stepStates[stage] === 'running') {
            var info = {
                visual: {icon:'👁️', label:'视觉分析'},
                speech:  {icon:'🎤', label:'语音分析'},
                gesture: {icon:'🤝', label:'手势分析'},
                scoring: {icon:'📊', label:'综合评分'},
                report:  {icon:'📝', label:'报告生成'}
            }[stage];
            if (info) {
                labelEl.innerHTML = '<strong>' + info.icon + ' ' + info.label + '</strong> ⏳ ' + percent + '%';
            }
        }
    }

    function updateStepCost(stage, costSeconds) {
        // 防御：仅接受正数耗时（忽略负数/NaN/Infinity/非数字）
        // 覆盖式写入，不累加——STEP_END 重复到达时最后一次为准
        var valid = (typeof costSeconds === 'number' && isFinite(costSeconds) && costSeconds > 0);
        if (valid) {
            stepCostMap[stage] = costSeconds;
        }
        safeRender(function(){
            var costEl = document.getElementById('cost-' + stage);
            if (costEl) {
                var cur = stepCostMap[stage];
                costEl.textContent = (typeof cur === 'number' && cur > 0) ? fmtSec(cur) : '';
            }
        });
    }

    // ── PIPELINE_DONE 延迟释放逻辑 ──
    var _pipelineDoneFired = false; // 防 DONE 重复触发

    function _onPipelineDone(overall, grade) {
        if (_pipelineDoneFired) {
            console.warn('[DONE] ignored duplicate _onPipelineDone');
            return;
        }
        _pipelineDoneFired = true;
        var ut = window.uiState;
        ut.stepBarrier = null;  // 清空 barrier
        _clearBarrierTimer();   // 取消任何未触发的超时定时器

        var timerKeys = (window._stageDisplayTimer && Object.keys(window._stageDisplayTimer)) || [];
        console.log('[DONE] _onPipelineDone overall=' + overall + ' grade=' + grade + ' barrierQ=' + barrierQueue.length + ' timers=' + timerKeys.length + ' steps=' + JSON.stringify(stepStates));

        // 动画管线：解锁全部 → rebuild → fade step UI → fade in report
        _finalizeVisualPipeline(overall, grade);
    }

    // ═══════════════════════════════════════════════════════════════
    // 2. Progressive State Reducer — 增量更新，不覆盖
    // ═══════════════════════════════════════════════════════════════
    // 前端 5 步状态机（visual/speech/gesture/scoring/report）。
    // calibration 是后端内部步骤，不参与 barrier / 步骤状态，统一在此过滤。
    var KNOWN_STAGES = {visual:1, speech:1, gesture:1, scoring:1, report:1};

    function reduce(evt) {
        var rawType = evt.event_type || evt.type || '';
        var norm = NORMALIZE[rawType] || rawType;
        var stage = evt.stage || '';
        var pl = evt.payload || {};
        var cost = evt.cost || 0;
        var changed = false;
        // [CP2] NORMALIZE + [CP3] REDUCE entry
        if (norm === 'STEP_START' || norm === 'STEP_END' || norm === 'DONE') {
            console.log('[REDUCE] event=' + norm + ' stage=' + stage + ' rawType=' + rawType + ' barrier=' + window.uiState.stepBarrier + ' queue=' + barrierQueue.length);
        }
        // 过滤未知 stage（如 calibration）——不参与步骤状态机，不设置 barrier
        if (stage && !KNOWN_STAGES[stage] && (norm === 'STEP_START' || norm === 'STEP_END' || norm === 'STEP_PROGRESS')) {
            console.log('[REDUCE] SKIP unknown stage=' + stage + ' event=' + norm);
            return;
        }

        switch (norm) {
        case 'SYSTEM_INIT':
            console.log('[INIT] protocol=' + (pl.protocol_version||'?') + ' build=' + (pl.build_id||'?'));
            // 重置 lastEventSeq，确保 Replay 事件不会被旧 seq 误丢
            window.uiState.lastEventSeq = 0;
            if (pl.protocol_version && pl.protocol_version !== '1.0') {
                updateStatusText('⚠️ 版本不匹配，刷新中...');
                setTimeout(function(){ location.reload(true); }, 2000);
            }
            break;

        case 'STEP_START':
            changed = setStepRunning(stage);
            console.log('[REDUCER] STEP_START stage=' + stage + ' changed=' + changed + ' barrier=' + window.uiState.stepBarrier);
            appendLog({level:'INFO', message:'▶ ' + stage + ' 分析开始'});
            break;

        case 'STEP_PROGRESS':
            if (stage && pl.percent !== undefined) {
                setStepProgress(stage, pl.percent);
            }
            break;

        case 'STEP_END':
            changed = setStepDone(stage, pl.status);
            if (pl.result) {
                DIM_ORDER.forEach(function(k){ if (pl.result[k] !== undefined) dims[k] = pl.result[k]; });
            }
            if (cost > 0) updateStepCost(stage, cost);
            window.runtimeState.dims = Object.assign({}, dims);
            window.runtimeState.stepStates = Object.assign({}, stepStates);
            window.runtimeState.stepCostMap = Object.assign({}, stepCostMap);
            console.log('[REDUCER] STEP_END stage=' + stage + ' changed=' + changed + ' stepStates=' + JSON.stringify(stepStates));
            if (changed) _onStepEndVisual(stage); // 启动 display timer
            appendLog({level:'INFO', message:'✓ ' + stage + ' ' + (pl.message || '完成') + (cost > 0 ? ' (' + fmtSec(cost) + ')' : '')});
            break;

        case 'DONE':
            // 禁止一次性 setAllStepsDone() — 各步骤已由 STEP_END 逐个完成
            appendLog({level:'INFO', message:'🎉 全部分析完成，总分: ' + (pl.overall_score||0).toFixed(1)});
            changed = true;  // 触发最后一次 rebuild
            // 同步 runtimeState
            window.runtimeState.overall = pl.overall_score || 0;
            window.runtimeState.grade = pl.grade || '';
            window.runtimeState.dims = Object.assign({}, dims);
            window.runtimeState.stepStates = Object.assign({}, stepStates);
            console.log('[REDUCER] DONE overall=' + (pl.overall_score||0) + ' grade=' + (pl.grade||'') + ' stepStates=' + JSON.stringify(stepStates));
            _onPipelineDone(pl.overall_score || 0, pl.grade || '');
            break;

        case 'METRIC':
            if (pl.name && pl.name.indexOf('confidence') >= 0) {
                dims['_confidence_' + pl.name] = pl.value;
            }
            if (cost > 0 && stage && stepStates[stage] === 'running') {
                updateStepCost(stage, cost);
            }
            break;

        case 'LOG':
            appendLog({level: pl.level || 'INFO', message: pl.message || ''});
            break;

        case 'ERROR':
            appendLog({level:'ERROR', message:'❌ ' + (pl.detail || pl.message || '未知错误')});
            break;

        case 'STATUS':
            // 重连场景: 只补升 waiting 状态，不覆盖 error
            if (pl.status === 'completed' && !rendered) {
                ['visual','speech','gesture','scoring','report'].forEach(function(s){
                    if (!stepStates[s] || stepStates[s] === 'waiting') {
                        stepStates[s] = 'done';
                    }
                });
                changed = true;
                _onPipelineDone(pl.overall_score || 0, pl.grade || '');
            } else if (pl.status && pl.status !== 'completed') {
                // 运行中重连: 不做任何 step 变更，只更新状态文字
                updateStatusText('⏳ 分析进行中...');
            }
            break;

        case 'HEARTBEAT':
            // 心跳保活，无需 UI 变更
            break;

        case 'STREAM_END':
            // 流结束信号，无需 UI 变更
            break;
        }

        if (changed) {
            console.log('[UI-STATE] rebuild phase=' + window.uiState.phase + ' barrier=' + window.uiState.stepBarrier + ' lock=' + window.uiState.lockRender + ' steps=' + JSON.stringify(stepStates));
            safeRender(function(){
                rebuildStepList(stepStates);
                updateStatusText(statusFromSteps(stepStates));
            });
        }
        // 每次处理后尝试释放 barrierQueue
        _drainBarrierQueue();
    }

    function statusFromSteps(steps) {
        var done = Object.values(steps).filter(function(s){return s==='done'||s==='error';}).length;
        var total = Object.keys(steps).length;
        if (done === total) return '✅ 全部完成 (' + done + '/' + total + ')';
        if (done > 0) return '⏳ 分析中 (' + done + '/' + total + ')';
        return '⏳ 等待分析开始...';
    }

    function updateStatusText(msg) {
        var el = document.getElementById('status-text');
        if (el) el.innerHTML = msg;
    }

    // ═══════════════════════════════════════════════════════════════
    // 3. Progressive Buffer Consumer — 逐事件消费，阶段间有延迟
    // ═══════════════════════════════════════════════════════════════
    function flushBuffer() {
        if (eventBuffer.length === 0) { bufferTimer = null; return; }
        var evt = eventBuffer.shift();
        processEvent(evt);
        // 阶段事件之间有延迟，日志事件立即处理
        var isStageEvent = (evt.event_type === 'STAGE_START' || evt.event_type === 'STAGE_END' ||
                           evt.event_type === 'PIPELINE_DONE');
        bufferTimer = setTimeout(flushBuffer, isStageEvent ? STAGE_DELAY : 50);
    }

    function enqueueEvent(evt) {
        eventBuffer.push(evt);
        if (!bufferTimer) flushBuffer();
    }

    // ═══════════════════════════════════════════════════════════════
    // 4. WebSocket — 连接 + 信封解析 + 事件入队
    // ═══════════════════════════════════════════════════════════════
    var wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var wsUrl = wsProto + '//' + location.host + '/ws/tasks/' + taskId;

    var wsConnected = false;  // WS/HTTP 互斥锁，防止双通道同时更新 UI
    var pollingTimer = null;  // HTTP 轮询定时器句柄，用于干净停止

    if (window.__activeWS && window.__activeWS.url === wsUrl) {
        window.__activeWS.close();
    }
    var ws = new WebSocket(wsUrl);
    window.__activeWS = ws;
    window.addEventListener('beforeunload', function(){
        wsConnected = false;
        if (pollingTimer) clearTimeout(pollingTimer);
        ws.close();
    });

    ws.onopen = function() {
        console.log('[WS] connected, buffer active');
        wsConnected = true;
        updateStatusText('✅ 已连接');
    };

    ws.onmessage = function(e) {
        try {
            var msg = JSON.parse(e.data);
            var evt = (msg.type === 'event' && msg.data) ? msg.data : msg;
            // [CP1] WS-RAW: 记录所有事件第一入口
            console.log('[WS-RAW] type=' + (evt.event_type||'?') + ' stage=' + (evt.stage||'?') + ' seq=' + (evt.seq||0) + ' payload=' + JSON.stringify(evt.payload||{}).substring(0,120));
            enqueueEvent(evt);
        } catch(ex) {
            console.error('[WS] parse error:', ex);
        }
    };

    ws.onclose = function() {
        wsConnected = false;
        if (pollingTimer) { clearTimeout(pollingTimer); pollingTimer = null; }
        console.log('[WS] closed, buf=' + eventBuffer.length + ' barrierQ=' + barrierQueue.length + ' dims=' + Object.keys(dims).length);
        // 先释放 barrierQueue 中被拦截的事件
        while (barrierQueue.length > 0) {
            var evt = barrierQueue.shift();
            reduce(evt);
        }
        // 紧急排空视觉管线，避免延迟渲染残留
        _abortVisualPipeline();
        // 消费完缓冲后仍未渲染 → HTTP 兜底
        if (!rendered && eventBuffer.length === 0) {
            fetchFinalResults(taskId);
        }
    };

    ws.onerror = function() {
        wsConnected = false;
        console.error('[WS] error, polling fallback');
        startPolling();
    };

    // ═══════════════════════════════════════════════════════════════
    // HTTP 轮询兜底
    // ═══════════════════════════════════════════════════════════════
    function startPolling() {
        // WS 已恢复 → 不启动轮询
        if (wsConnected) return;
        updateStatusText('⏳ WebSocket 断开，HTTP 轮询中...');
        function poll() {
            // 每次轮询前检查：WS 恢复则停止轮询
            if (rendered || wsConnected) return;
            fetch('/api/tasks/' + taskId).then(function(r){return r.json();}).then(function(task){
                if (rendered || wsConnected) return;
                if (task.status === 'completed') {
                    updateStatusText('✅ 分析完成，加载报告...');
                    fetchFinalResults(taskId);
                } else if (task.status !== 'failed') {
                    updateStatusText('⏳ HTTP 轮询中... (' + statusLabel(task.status) + ')');
                    pollingTimer = setTimeout(poll, 2000);
                } else {
                    updateStatusText('❌ 分析失败');
                }
            }).catch(function(){
                if (rendered || wsConnected) return;
                updateStatusText('⚠️ 网络异常，重试中...');
                pollingTimer = setTimeout(poll, 3000);
            });
        }
        poll();
    }

    function fetchFinalResults(tid) {
        if (rendered) return;
        rendered = true;
        window.uiState.lockRender = true;
        // 同步 runtimeState
        window.runtimeState.rendered = true;

        fetch('/api/tasks/' + tid + '/score').then(function(r){return r.json();}).then(function(score){
            fetch('/api/tasks/' + tid).then(function(r){return r.json();}).then(function(task){
                // Merge: HTTP 为权威数据，WS 累积值填补缺失
                var mergedDims = {};
                var wsState = window.runtimeState || {};
                // 先取 WS 累积
                var wsDims = wsState.dims || {};
                Object.keys(wsDims).forEach(function(k){ mergedDims[k] = wsDims[k]; });
                // HTTP dimension_scores 覆盖（权威）
                var httpDims = score.dimension_scores || {};
                Object.keys(httpDims).forEach(function(k){ mergedDims[k] = httpDims[k]; });

                renderResults(tid,
                    score.overall_score || wsState.overall || 0,
                    score.grade || wsState.grade || '',
                    mergedDims,
                    score.weights_used || {},
                    task.timing || wsState.timing || {}
                );

                window.uiState.lockRender = false;
                window.uiState.phase = 'done';
            }).catch(function(innerErr){
                // 内层 fetch 失败：确保 lockRender 被恢复，避免 UI 永久冻结
                console.error('[FETCH] 内层 /tasks fetch 失败，重试:', innerErr);
                window.uiState.lockRender = false;
                rendered = false;
                window.runtimeState.rendered = false;
                setTimeout(function(){ fetchFinalResults(tid); }, 2000);
            });
        }).catch(function(outerErr){
            // 外层 fetch 失败
            console.error('[FETCH] 外层 /score fetch 失败，重试:', outerErr);
            setTimeout(function(){
                rendered = false;
                window.uiState.lockRender = false;
                window.runtimeState.rendered = false;  // 保持 runtimeState 与本地 rendered 一致
                fetchFinalResults(tid);
            }, 2000);
        });
    }
}

function statusLabel(st) {
    var map = {pending:'等待中', uploaded:'已上传', visual_running:'视觉分析中', speech_running:'语音分析中',
               gesture_running:'手势分析中', scoring:'评分中', reporting:'报告生成中', completed:'已完成', failed:'失败'};
    return map[st] || st;
}

function rebuildStepList(steps) {
    var items = [
        {key:'visual', label:'视觉分析', icon:'👁️', desc:'眼神交流 + 头部姿态'},
        {key:'speech',  label:'语音分析', icon:'🎤', desc:'转录 + 语速 + 音高 + 填充词'},
        {key:'gesture', label:'手势分析', icon:'🤝', desc:'手部关键点检测 + 分类'},
        {key:'scoring',  label:'综合评分', icon:'📊', desc:'加权计算 + 校验'},
        {key:'report',  label:'报告生成', icon:'📝', desc:'中文演讲反馈报告'}
    ];
    var diag = [];
    items.forEach(function(item){
        var s = steps[item.key] || 'waiting';
        var dotEl = document.getElementById('dot-' + item.key);
        var rowEl = document.getElementById('row-' + item.key);
        var costEl = document.getElementById('cost-' + item.key);
        var labelEl = document.getElementById('label-' + item.key);
        // [DOM-DIAG] 记录每次 rebuild 的 DOM 状态
        diag.push(item.key + '=' + s + ' dot=' + (dotEl ? dotEl.className : 'NULL') + ' row=' + (rowEl ? rowEl.className : 'NULL'));

        if (s === 'done' && window._stageDisplayTimer[item.key]) {
            // data=done 但 display timer 仍在倒计时 → 保持 running 动画
            if (dotEl) { dotEl.className = 'step-dot running'; dotEl.textContent = ''; }
            if (rowEl) rowEl.className = 'step-item running';
            if (labelEl) labelEl.innerHTML = '<strong>' + item.icon + ' ' + item.label + '</strong> ⏳ 即将完成...';
            if (costEl) {
                var cost = stepCostMap[item.key];
                costEl.textContent = (cost > 0) ? fmtSec(cost) : '';
            }
        } else if (s === 'done') {
            if (dotEl) { dotEl.className = 'step-dot ok'; dotEl.textContent = '✓'; }
            if (rowEl) rowEl.className = 'step-item done';
            if (labelEl) labelEl.innerHTML = '<strong>' + item.icon + ' ' + item.label + '</strong> ✅';
            if (costEl) {
                var cost = stepCostMap[item.key];
                costEl.textContent = (cost > 0) ? fmtSec(cost) : '';
            }
        } else if (s === 'error') {
            // 使用独立 .step-dot.error 样式（红色），避免误导用户为成功
            if (dotEl) { dotEl.className = 'step-dot error'; dotEl.textContent = '✗'; }
            if (rowEl) rowEl.className = 'step-item error';
            if (labelEl) labelEl.innerHTML = '<strong>' + item.icon + ' ' + item.label + '</strong> ❌';
            if (costEl) costEl.textContent = '失败';
        } else if (s === 'running') {
            if (dotEl) { dotEl.className = 'step-dot running'; dotEl.textContent = ''; }
            if (rowEl) rowEl.className = 'step-item running';
            if (labelEl) labelEl.innerHTML = '<strong>' + item.icon + ' ' + item.label + '</strong> ⏳ 进行中...';
        } else {
            if (dotEl) { dotEl.className = 'step-dot waiting'; dotEl.textContent = ''; }
            if (rowEl) rowEl.className = 'step-item waiting';
            if (labelEl) labelEl.innerHTML = '<strong>' + item.icon + ' ' + item.label + '</strong><br><small style=\"color:var(--text-dim)\">' + item.desc + '</small>';
        }
    });
    // [DOM-DIAG] 输出完整 DOM 状态快照
    var missing = diag.filter(function(d){ return d.indexOf('NULL') >= 0; });
    if (missing.length > 0) console.warn('[DOM-DIAG] MISSING elements:', missing.join(' | '));
    console.log('[DOM-DIAG]', diag.join(' | '));
    // [CP5] REBUILD summary
    console.log('[REBUILD] visual=' + (steps.visual||'?') + ' speech=' + (steps.speech||'?') + ' gesture=' + (steps.gesture||'?') + ' scoring=' + (steps.scoring||'?') + ' report=' + (steps.report||'?') + '  stepStates=' + JSON.stringify(steps));
    // [CP6] DOM class + computedStyle readback — 延迟到下一个动画帧，等浏览器完成样式计算
    var _steps = steps;
    requestAnimationFrame(function(){
        ['visual','speech','gesture','scoring','report'].forEach(function(s){
            var dot = document.getElementById('dot-' + s);
            var row = document.getElementById('row-' + s);
            var label = document.getElementById('label-' + s);
            var csDot = dot ? getComputedStyle(dot) : null;
            var csRow = row ? getComputedStyle(row) : null;
            var csLabel = label ? getComputedStyle(label) : null;
            console.log('[DOM-DIAG] stage=' + s +
                ' state=' + (_steps[s]||'?') +
                ' dot.class=' + (dot ? dot.className : 'NULL') +
                ' dot.bg=' + (csDot ? csDot.backgroundColor : 'NULL') +
                ' dot.opacity=' + (csDot ? csDot.opacity : 'NULL') +
                ' row.class=' + (row ? row.className : 'NULL') +
                ' row.bg=' + (csRow ? csRow.backgroundColor : 'NULL') +
                ' row.opacity=' + (csRow ? csRow.opacity : 'NULL') +
                ' label.color=' + (csLabel ? csLabel.color : 'NULL'));
        });
    });
}

// ── 构建步骤条 ──
function buildStepList(steps) {
    var items = [
        {key:'visual', label:'👁️ 视觉分析', desc:'眼神交流 + 头部姿态'},
        {key:'speech',  label:'🎤 语音分析', desc:'转录 + 语速 + 音高 + 填充词'},
        {key:'gesture', label:'🤝 手势分析', desc:'手部关键点检测 + 分类'},
        {key:'scoring',  label:'📊 综合评分', desc:'加权计算 + 校验'},
        {key:'report',  label:'📝 报告生成', desc:'中文演讲反馈报告'}
    ];
    return '<div class="step-list">' + items.map(function(item){
        var s = steps[item.key] || 'waiting';
        var dotCls = s === 'done' ? 'ok' : (s === 'running' ? 'running' : 'waiting');
        var rowCls = s === 'done' ? 'done' : (s === 'running' ? 'running' : 'waiting');
        var dotText = s === 'done' ? '✓' : '';
        return '<div class="step-item '+rowCls+'" id="row-'+item.key+'">' +
            '<div class="step-dot '+dotCls+'" id="dot-'+item.key+'">'+dotText+'</div>' +
            '<div class="step-label" id="label-'+item.key+'"><strong>'+item.label+'</strong><br><small style="color:var(--text-dim)">'+item.desc+'</small></div>' +
            '<div class="step-cost" id="cost-'+item.key+'"></div></div>';
    }).join('') + '</div><div class="log-panel" id="log-panel"><div class="log-line">等待日志...</div></div>';
}

// ── 添加日志 ──
function appendLog(msg) {
    var panel = document.getElementById('log-panel');
    if (!panel) return;
    var cls = msg.level === 'ERROR' ? 'log-err' : (msg.level === 'WARNING' ? 'log-warn' : '');
    panel.innerHTML += '<div class="log-line '+cls+'">[' + msg.level + '] ' + (msg.message||'') + '</div>';
    panel.scrollTop = panel.scrollHeight;
}

// ── 加载最终结果 ──
var _loadFinalRetries = 0;
var _loadFinalMaxRetries = 15; // 最多重试 30 秒

async function loadFinalResults(taskId, doneMsg, dims) {
    try {
        var [taskResp, scoreResp] = await Promise.all([
            fetch('/api/tasks/' + taskId).then(function(r){return r.json();}),
            fetch('/api/tasks/' + taskId + '/score').then(function(r){return r.ok?r.json():null;}).catch(function(){return null;})
        ]);

        if (!scoreResp) {
            _loadFinalRetries++;
            if (_loadFinalRetries > _loadFinalMaxRetries) {
                console.error('[FETCH] score 端点重试超限，停止');
                updateStatusText('❌ 加载报告超时，请刷新页面');
                return;
            }
            console.warn('[FETCH] score 未就绪，重试 ' + _loadFinalRetries + '/' + _loadFinalMaxRetries);
            setTimeout(function(){ loadFinalResults(taskId, doneMsg, dims); }, 2000);
            return;
        }
        _loadFinalRetries = 0; // 成功后重置

        var score = scoreResp;
        var overall = score.overall_score || 0;
        var grade = score.grade || '';

        // Merge: WS 累积 dims 为基底，HTTP 权威数据覆盖
        var sdims = {};
        var wsAccum = dims || {};
        Object.keys(wsAccum).forEach(function(k){ sdims[k] = wsAccum[k]; });
        var httpDims = score.dimension_scores || {};
        Object.keys(httpDims).forEach(function(k){ sdims[k] = httpDims[k]; });

        var weights = score.weights_used || {};
        var timing = taskResp.timing || {};

        // 同步 runtimeState
        window.runtimeState.dims = sdims;
        window.runtimeState.overall = overall;
        window.runtimeState.grade = grade;
        window.runtimeState.timing = timing;
        window.runtimeState.rendered = true;

        // 数据就绪 → fade-out 步骤条 → 替换为报告
        var container = document.getElementById('task-container');
        var stepList = container ? container.querySelector('.step-list') : null;
        if (stepList) stepList.classList.add('pipeline-exit');
        setTimeout(function(){
            renderResults(taskId, overall, grade, sdims, weights, timing);
        }, 400); // 等 fade-out 动画播完再替换

    } catch(e) {
        window.runtimeState.rendered = false;
        var container = document.getElementById('task-container');
        if (container) container.innerHTML = '<p class="loading-text">❌ 结果加载失败: ' + e.message + '</p>';
    }
}

// ── 渲染完整结果页 ──
function renderResults(taskId, overall, grade, dims, weights, timing) {
    var container = document.getElementById('task-container');
    if (!container) return;  // 防御：非任务详情页不渲染
    var lv = getLevel(overall);
    var totalStr = timing.total ? fmtSec(timing.total) : '—';

    var html = '';

    // Hero 卡片 — 聚光灯下的分数
    html += '<div class="score-hero pipeline-enter">';
    html += '<div class="hero-label">综合得分</div>';
    html += '<div class="big-number">' + overall.toFixed(1) + '</div>';
    html += '<div class="score-unit">满分 100</div>';
    html += '<div class="grade-badge">' + lv.emoji + ' ' + grade + '</div>';
    html += '<div class="timing-info">⏱ 总耗时 ' + totalStr + '</div>';
    html += '</div>';

    // 四维卡片
    html += '<div class="dim-grid">';
    DIM_ORDER.forEach(function(k){
        var d = DIM_LABELS[k], v = dims[k]||0, l = getLevel(v);
        html += '<div class="dim-card"><div class="dim-icon">'+d.icon+'</div><div class="dim-name">'+d.name+'</div>';
        html += '<div class="dim-score">'+v.toFixed(1)+'</div><div class="dim-grade" style="color:'+l.color+';">'+l.emoji+' '+l.label+'</div>';
        html += '<div class="dim-bar"><div class="dim-bar-fill" style="width:'+v+'%;background:'+l.color+';"></div></div></div>';
    });
    html += '</div>';

    // 图表
    html += '<div class="chart-grid">';
    html += '<div class="chart-box"><div class="chart-title">🎯 四维能力雷达图</div><div id="chart-radar" style="height:350px;"></div></div>';
    html += '<div class="chart-box"><div class="chart-title">📊 维度得分对比</div><div id="chart-bar" style="height:350px;"></div></div>';
    html += '<div class="chart-box"><div class="chart-title">🍩 评分权重分布</div><div id="chart-pie" style="height:350px;"></div></div>';
    html += '</div>';

    // 各模块耗时
    html += '<div class="insight-grid">';
    html += '<div class="insight-box"><div class="insight-label" style="color:var(--accent);">⏱ 各模块耗时</div>';
    html += '<table class="task-table"><thead><tr><th>模块</th><th>耗时</th><th>得分</th></tr></thead><tbody>';
    var STEP_DIM_MAP = {visual: 'eye_contact_score', speech: 'speech_score', gesture: 'gesture_score'};
    [['visual','视觉分析'],['speech','语音分析'],['gesture','手势分析'],['scoring','综合评分'],['report','报告生成']].forEach(function(p){
        var t = timing[p[0]] ? fmtSec(timing[p[0]]) : '—';
        var dimKey = STEP_DIM_MAP[p[0]];
        var s = dimKey ? dims[dimKey] : undefined;
        html += '<tr><td>'+p[1]+'</td><td>'+t+'</td><td>'+(s!==undefined?s.toFixed(1):'—')+'</td></tr>';
    });
    html += '</tbody></table></div>';

    // 报告链接
    html += '<div class="insight-box" style="display:flex;align-items:center;justify-content:center;gap:1rem;">';
    html += '<a class="btn-sm" href="/api/tasks/'+taskId+'/report" target="_blank">📝 查看完整报告</a>';
    html += '<a class="btn-sm" href="/api/tasks/'+taskId+'/score" target="_blank">📊 查看评分 JSON</a>';
    html += '</div></div>';

    container.innerHTML = html;

    // 画图表
    try {
        if (typeof Plotly !== 'undefined') {
            plotRadar('chart-radar', dims);
            plotBar('chart-bar', dims);
            plotWeights('chart-pie', weights);
        }
    } catch(e) { console.error('Plotly error:', e); }
}

})();
