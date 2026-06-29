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
    if (score >= 85) return {label:'优秀', color:'#10B981', emoji:'⭐'};
    if (score >= 70) return {label:'良好', color:'#06B6D4', emoji:'✅'};
    if (score >= 50) return {label:'一般', color:'#F59E0B', emoji:'⚠️'};
    return {label:'需改进', color:'#EF4444', emoji:'📉'};
}

function statusBadge(status) {
    if (status === 'completed') return '<span class="badge ok">已完成</span>';
    if (status === 'failed') return '<span class="badge failed">失败</span>';
    return '<span class="badge running">进行中</span>';
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
var DIM_COLORS = ['#4F46E5','#10B981','#F59E0B','#06B6D4'];

function plotRadar(divId, dims) {
    var labels = DIM_ORDER.map(function(k){return DIM_LABELS[k].name;});
    var values = DIM_ORDER.map(function(k){return dims[k]||0;});
    values.push(values[0]);
    var data = [{
        type:'scatterpolar', r:values, theta:labels.concat([labels[0]]),
        fill:'toself', fillcolor:'rgba(79,70,229,0.3)',
        line:{color:'#4F46E5',width:3},
        marker:{size:8,color:'#4F46E5'}
    }];
    var layout = {
        polar:{radialaxis:{range:[0,100],tickfont:{color:'#94A3B8',size:10},gridcolor:'rgba(148,163,184,0.12)'},
               angularaxis:{tickfont:{color:'#F1F5F9',size:12},gridcolor:'rgba(148,163,184,0.12)'},
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
        marker:{color:DIM_COLORS, line:{color:'rgba(255,255,255,0.15)',width:1}},
        text:values.map(function(v){return v.toFixed(1);}), textposition:'outside',
        textfont:{color:'#F1F5F9',size:14}
    }];
    var layout = {
        yaxis:{range:[0,110],tickfont:{color:'#94A3B8'},gridcolor:'rgba(148,163,184,0.1)'},
        xaxis:{tickfont:{color:'#F1F5F9',size:12}},
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
    var values = DIM_ORDER.map(function(k){return weights[k]||0;});
    var data = [{
        type:'pie', labels:labels, values:values,
        marker:{colors:DIM_COLORS, line:{color:'rgba(0,0,0,0.3)',width:1}},
        textinfo:'label+percent', textfont:{color:'#F1F5F9',size:12}, hole:0.55
    }];
    var layout = {paper_bgcolor:'rgba(0,0,0,0)', margin:{l:10,r:10,t:10,b:10}, height:350, showlegend:false};
    Plotly.newPlot(divId, data, layout, {displayModeBar:false, responsive:true});
}

// ═══════════════════════════════════════════════════════════════════════════
// 上传页
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
    var taskTbody = document.getElementById('task-tbody');
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

    function loadTaskList(tbody) {
        fetch('/api/tasks?limit=20').then(function(r){return r.json();}).then(function(tasks){
            if (!tasks.length) { tbody.innerHTML = '<tr><td colspan="6" class="empty-row">暂无分析任务</td></tr>'; return; }
            tbody.innerHTML = tasks.map(function(t){
                var s = t.status, sc = t.overall_score != null ? t.overall_score.toFixed(1) : '—';
                return '<tr><td>'+(t.filename||'—')+'</td><td>'+statusBadge(s)+'</td><td>'+sc+'</td><td>'+(t.grade||'—')+'</td><td>'+fmtDate(t.created_at)+'</td><td>'+(s==='completed'?'<a class="btn-sm" href="/tasks/'+t.id+'">查看</a>':'—')+'</td></tr>';
            }).join('');
        }).catch(function(e){ tbody.innerHTML = '<tr><td colspan="6" class="empty-row">加载失败</td></tr>'; });
    }

    if (taskTbody) { loadTaskList(taskTbody); setInterval(function(){loadTaskList(taskTbody);}, 5000); }

    var histBody = document.getElementById('history-tbody');
    if (histBody) { loadTaskList(histBody); }
}

// ═══════════════════════════════════════════════════════════════════════════
// 任务详情页 (WebSocket)
// ═══════════════════════════════════════════════════════════════════════════
if (typeof TASK_ID !== 'undefined') { initTaskPage(TASK_ID); }

function initTaskPage(taskId) {
    var container = document.getElementById('task-container');
    var dims = {};
    var rendered = false;
    var stepStates = {visual:'waiting',speech:'waiting',gesture:'waiting',scoring:'waiting',report:'waiting'};

    container.innerHTML = '<p class="loading-text" id="status-text"><span class="spinner"></span>正在连接...</p>' +
        buildStepList({visual:'waiting',speech:'waiting',gesture:'waiting',scoring:'waiting',report:'waiting'});

    // ═══════════════════════════════════════════════════════════════
    // 1. Event Timeline Buffer — 存储所有事件，支持渐进式回放
    // ═══════════════════════════════════════════════════════════════
    var eventBuffer = [];
    var bufferTimer = null;
    var STAGE_DELAY = 400; // 阶段间动画延迟 (ms)

    var NORMALIZE = {
        STAGE_START: 'STEP_START', STAGE_END: 'STEP_END',
        PIPELINE_DONE: 'DONE', METRIC: 'METRIC', LOG: 'LOG', ERROR: 'ERROR',
        STATUS: 'STATUS', HEARTBEAT: 'HEARTBEAT', STREAM_END: 'STREAM_END',
        SYSTEM_INIT: 'SYSTEM_INIT'
    };

    // ═══════════════════════════════════════════════════════════════
    // 2. Progressive State Reducer — 增量更新，不覆盖
    // ═══════════════════════════════════════════════════════════════
    function reduce(evt) {
        var rawType = evt.event_type || evt.type || '';
        var norm = NORMALIZE[rawType] || rawType;
        var stage = evt.stage || '';
        var pl = evt.payload || {};
        var changed = false;

        switch (norm) {
        case 'SYSTEM_INIT':
            console.log('[INIT] protocol=' + (pl.protocol_version||'?') + ' build=' + (pl.build_id||'?'));
            if (pl.protocol_version && pl.protocol_version !== '1.0') {
                updateStatusText('⚠️ 版本不匹配，刷新中...');
                setTimeout(function(){ location.reload(true); }, 2000);
            }
            break;

        case 'STEP_START':
            if (stepStates[stage] !== 'running') {
                stepStates[stage] = 'running';
                changed = true;
            }
            appendLog({level:'INFO', message:'▶ ' + stage + ' 分析开始'});
            break;

        case 'STEP_END':
            if (stepStates[stage] !== 'done') {
                stepStates[stage] = (pl.status === 'failed') ? 'error' : 'done';
                changed = true;
            }
            if (pl.result) {
                DIM_ORDER.forEach(function(k){ if (pl.result[k] !== undefined) dims[k] = pl.result[k]; });
            }
            appendLog({level:'INFO', message:'✓ ' + stage + ' ' + (pl.message || '完成')});
            break;

        case 'DONE':
            stepStates = {visual:'done',speech:'done',gesture:'done',scoring:'done',report:'done'};
            changed = true;
            updateStatusText('✅ 分析完成，加载报告...');
            appendLog({level:'INFO', message:'🎉 全部分析完成，总分: ' + (pl.overall_score||0).toFixed(1)});
            // 延迟跳转报告，让打勾动画先展示
            setTimeout(function(){
                loadFinalResults(taskId, {overall_score: pl.overall_score, grade: pl.grade}, dims);
            }, 600);
            break;

        case 'METRIC':
            if (pl.name && pl.name.indexOf('confidence') >= 0) {
                dims['_confidence_' + pl.name] = pl.value;
            }
            break;

        case 'LOG':
            appendLog({level: pl.level || 'INFO', message: pl.message || ''});
            break;

        case 'ERROR':
            appendLog({level:'ERROR', message:'❌ ' + (pl.detail || pl.message || '未知错误')});
            break;

        case 'STATUS':
            if (pl.status === 'completed' && !rendered) {
                stepStates = {visual:'done',speech:'done',gesture:'done',scoring:'done',report:'done'};
                changed = true;
                updateStatusText('✅ 已完成，加载报告...');
                setTimeout(function(){
                    loadFinalResults(taskId, {overall_score: pl.overall_score, grade: pl.grade}, dims);
                }, 400);
            }
            break;
        }

        if (changed) {
            rebuildStepList(stepStates);
            updateStatusText(statusFromSteps(stepStates));
        }
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
        reduce(evt);
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

    if (window.__activeWS && window.__activeWS.url === wsUrl) {
        window.__activeWS.close();
    }
    var ws = new WebSocket(wsUrl);
    window.__activeWS = ws;
    window.addEventListener('beforeunload', function(){ ws.close(); });

    ws.onopen = function() {
        console.log('[WS] connected, buffer active');
        updateStatusText('✅ 已连接');
    };

    ws.onmessage = function(e) {
        try {
            var msg = JSON.parse(e.data);
            var evt = (msg.type === 'event' && msg.data) ? msg.data : msg;
            enqueueEvent(evt);
        } catch(ex) {
            console.error('[WS] parse error:', ex);
        }
    };

    ws.onclose = function() {
        console.log('[WS] closed, buffer=' + eventBuffer.length + ' dims=' + Object.keys(dims).length);
        // 消费完缓冲后仍未渲染 → HTTP 兜底
        if (!rendered && eventBuffer.length === 0) {
            fetchFinalResults(taskId);
        }
    };

    ws.onerror = function() {
        console.error('[WS] error, polling fallback');
        startPolling();
    };

    // ═══════════════════════════════════════════════════════════════
    // HTTP 轮询兜底
    // ═══════════════════════════════════════════════════════════════
    function startPolling() {
        function poll() {
            if (rendered) return;
            fetch('/api/tasks/' + taskId).then(function(r){return r.json();}).then(function(task){
                if (task.status === 'completed') fetchFinalResults(taskId);
                else if (task.status !== 'failed') setTimeout(poll, 2000);
            }).catch(function(){ setTimeout(poll, 3000); });
        }
        poll();
    }

    function fetchFinalResults(tid) {
        if (rendered) return;
        rendered = true;
        fetch('/api/tasks/' + tid + '/score').then(function(r){return r.json();}).then(function(score){
            fetch('/api/tasks/' + tid).then(function(r){return r.json();}).then(function(task){
                renderResults(tid, score.overall_score, score.grade,
                    score.dimension_scores || {}, score.weights_used || {}, task.timing || {});
            });
        }).catch(function(){
            setTimeout(function(){ rendered = false; fetchFinalResults(tid); }, 2000);
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
    items.forEach(function(item){
        var s = steps[item.key] || 'waiting';
        var dotEl = document.getElementById('dot-' + item.key);
        var rowEl = document.getElementById('row-' + item.key);
        var costEl = document.getElementById('cost-' + item.key);
        var labelEl = document.getElementById('label-' + item.key);

        if (s === 'done') {
            if (dotEl) { dotEl.className = 'step-dot ok'; dotEl.textContent = '✓'; }
            if (rowEl) rowEl.className = 'step-item done';
            if (labelEl) labelEl.innerHTML = '<strong>' + item.icon + ' ' + item.label + '</strong> ✅';
        } else if (s === 'running') {
            if (dotEl) { dotEl.className = 'step-dot running'; dotEl.textContent = ''; }
            if (rowEl) rowEl.className = 'step-item running';
            if (labelEl) labelEl.innerHTML = '<strong>' + item.icon + ' ' + item.label + '</strong> ⏳ 进行中...';
        } else {
            if (dotEl) { dotEl.className = 'step-dot waiting'; dotEl.textContent = ''; }
            if (rowEl) rowEl.className = 'step-item waiting';
            if (labelEl) labelEl.innerHTML = '<strong>' + item.icon + ' ' + item.label + '</strong><br><small style=\"color:var(--muted)\">' + item.desc + '</small>';
        }
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
            '<div class="step-label" id="label-'+item.key+'"><strong>'+item.label+'</strong><br><small style="color:var(--muted)">'+item.desc+'</small></div>' +
            '<div class="step-cost" id="cost-'+item.key+'"></div></div>';
    }).join('') + '</div><div class="log-panel" id="log-panel"><div class="log-line">等待日志...</div></div>';
}

// ── 更新进度 ──
function updateProgress(msg) {
    var step = msg.step, status = msg.status;
    // 更新步骤点
    var items = document.querySelectorAll('.step-item');
    items.forEach(function(el){
        var key = el.querySelector('.step-cost')?.id?.replace('cost-','');
        if (key === step) {
            el.className = 'step-item ' + (status === 'done' ? 'done' : (status === 'running' ? 'running' : (status === 'error' ? 'done' : 'waiting')));
            var dot = el.querySelector('.step-dot');
            if (dot) dot.className = 'step-dot ' + (status === 'done' ? 'ok' : (status === 'running' ? 'running' : (status === 'error' ? 'ok' : 'waiting')));
        }
    });
    // 更新耗时和消息
    if (status === 'done' || status === 'error') {
        var costEl = document.getElementById('cost-' + step);
        if (costEl && msg.message) costEl.textContent = msg.message;
    }
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
async function loadFinalResults(taskId, doneMsg, dims) {
    try {
        var [taskResp, scoreResp] = await Promise.all([
            fetch('/api/tasks/' + taskId).then(function(r){return r.json();}),
            fetch('/api/tasks/' + taskId + '/score').then(function(r){return r.ok?r.json():null;}).catch(function(){return null;})
        ]);

        if (!scoreResp) {
            // 评分未就绪，延迟重试
            setTimeout(function(){ loadFinalResults(taskId, doneMsg, dims); }, 2000);
            return;
        }

        var score = scoreResp;
        var overall = score.overall_score || 0;
        var grade = score.grade || '';
        var sdims = score.dimension_scores || dims;
        var weights = score.weights_used || {};
        var timing = taskResp.timing || {};

        renderResults(taskId, overall, grade, sdims, weights, timing);

    } catch(e) {
        var container = document.getElementById('task-container');
        if (container) container.innerHTML = '<p class="loading-text">❌ 结果加载失败: ' + e.message + '</p>';
    }
}

// ── 渲染完整结果页 ──
function renderResults(taskId, overall, grade, dims, weights, timing) {
    var container = document.getElementById('task-container');
    var lv = getLevel(overall);
    var totalStr = timing.total ? fmtSec(timing.total) : '—';

    var html = '';

    // Hero 卡片
    html += '<div class="score-hero">';
    html += '<div style="font-size:0.85rem;opacity:0.8;letter-spacing:0.1em;">综合得分</div>';
    html += '<div class="big-number">' + overall.toFixed(1) + '</div>';
    html += '<div style="font-size:0.9rem;opacity:0.75;">满分 100</div>';
    html += '<div class="grade-badge">' + lv.emoji + ' ' + grade + '</div>';
    html += '<div class="timing-info">⏱ 总耗时: ' + totalStr + '</div>';
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
    [['visual','视觉分析'],['speech','语音分析'],['gesture','手势分析'],['scoring','综合评分'],['report','报告生成']].forEach(function(p){
        var t = timing[p[0]] ? fmtSec(timing[p[0]]) : '—';
        var s = dims[DIM_ORDER[['visual','speech','gesture','scoring','report'].indexOf(p[0])]];
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

// ── 轮询回退 ──
function pollTaskResults(taskId, dims) {
    var attempts = 0, max = 60;
    var interval = setInterval(async function(){
        attempts++;
        try {
            var resp = await fetch('/api/tasks/' + taskId + '/status');
            var st = await resp.json();
            if (st.status === 'completed' || st.status === 'failed' || attempts >= max) {
                clearInterval(interval);
                loadFinalResults(taskId, null, dims);
            }
        } catch(e) { clearInterval(interval); }
    }, 2000);
}
})();
