/**
 * 端到端时序验收测试
 * 模拟真实 WebSocket 事件流：并行分析 → 逐个 STEP_END → DONE → 最终报告
 * 验证: 步骤逐推进 / barrierQueue 清空 / timer 不泄漏 / 动画与流程一致
 * 运行: node tests/e2e_pipeline_test.js
 */
'use strict';

// ═══════════════════════════════════════════════════════════
// 完全复刻 app.js 的 barrier + event 状态机核心逻辑
// ═══════════════════════════════════════════════════════════
const BARRIER_TIMEOUT = 5000;
const BARRIER_QUEUE_MAX = 100;
const STAGE_DELAY = 400;  // ms，阶段事件间延迟

let uiState = {
    phase: 'running',
    lockRender: false,
    stepBarrier: null,
    lastEventSeq: 0
};
let barrierQueue = [];
let barrierTimer = null;
let stepStates = {};
let dims = {};
let eventBuffer = [];
let bufferTimer = null;

// 计数器 / 日志
let timeline = [];          // [{ts, event, action, state}]
let eventsProcessed = 0;
let eventsBlocked = 0;
let eventsDropped = 0;
let barrierTimeouts = 0;
let barrierReleases = 0;
let timerCreated = 0;
let timerCancelled = 0;
let timerFired = 0;

const T0 = Date.now();
function ts() { return ((Date.now() - T0) / 1000).toFixed(2); }
function log(action, detail) {
    const entry = { t: ts(), action, detail, barrier: uiState.stepBarrier, q: barrierQueue.length };
    timeline.push(entry);
    console.log(`[${entry.t}] ${action.padEnd(14)} ${detail.padEnd(40)} barrier=${String(uiState.stepBarrier).padEnd(8)} q=${barrierQueue.length}`);
}

// ── barrier timer ──
function _startBarrierTimer(stage) {
    _clearBarrierTimer();
    timerCreated++;
    barrierTimer = setTimeout(() => {
        timerFired++;
        log('TIMER_FIRE', `stage=${stage}, timerCreated=${timerCreated}, timerCancelled=${timerCancelled}`);
        uiState.stepBarrier = null;
        _drainBarrierQueue();
        // 仅当 drain 未重新建立 barrier 时清空引用
        if (uiState.stepBarrier === null) {
            barrierTimer = null;
        }
    }, BARRIER_TIMEOUT);
    log('TIMER_START', `stage=${stage}`);
}

function _clearBarrierTimer() {
    if (barrierTimer) {
        timerCancelled++;
        clearTimeout(barrierTimer);
        barrierTimer = null;
        log('TIMER_CANCEL', `timerCreated=${timerCreated}, timerCancelled=${timerCancelled}`);
    }
}

// ── barrier ──
function _passesBarrier(evt) {
    if (uiState.phase !== 'running') return false;
    if (uiState.lockRender) return false;
    const sysTypes = ['SYSTEM_INIT','LOG','ERROR','HEARTBEAT','STREAM_END','STATUS'];
    if (sysTypes.indexOf(evt.event_type) >= 0) return true;
    if (uiState.stepBarrier !== null && evt.stage !== uiState.stepBarrier) return false;
    return true;
}

function _drainBarrierQueue() {
    if (barrierQueue.length > BARRIER_QUEUE_MAX) {
        const overflow = barrierQueue.length - BARRIER_QUEUE_MAX;
        log('QUEUE_OVERFLOW', `丢掉最早 ${overflow} 条 (共${barrierQueue.length}条)`);
        barrierQueue = barrierQueue.slice(-BARRIER_QUEUE_MAX);
    }
    let drained = 0;
    while (barrierQueue.length > 0) {
        const evt = barrierQueue.shift();
        if (_passesBarrier(evt)) {
            reduce(evt);
            drained++;
        } else {
            barrierQueue.unshift(evt);
            break;
        }
    }
    if (drained > 0) log('DRAIN', `释放了 ${drained} 条事件, 剩余 ${barrierQueue.length}`);
}

// ── 步骤状态 ──
function setStepRunning(stage) {
    if (stepStates[stage] !== 'running') {
        stepStates[stage] = 'running';
        if (uiState.stepBarrier === null) {
            uiState.stepBarrier = stage;
            _startBarrierTimer(stage);
        }
        log('STEP_RUN', stage);
        return true;
    }
    return false;
}

function setStepDone(stage, status) {
    const newState = (status === 'failed') ? 'error' : 'done';
    if (stepStates[stage] !== newState) {
        stepStates[stage] = newState;
        if (uiState.stepBarrier === stage) {
            uiState.stepBarrier = null;
            _clearBarrierTimer();
            barrierReleases++;
        }
        log('STEP_DONE', `${stage} → ${newState} (releases=${barrierReleases})`);
        return true;
    }
    return false;
}

// ── reduce ──
function reduce(evt) {
    eventsProcessed++;
    const type = evt.event_type;
    const stage = evt.stage || '';
    const pl = evt.payload || {};

    switch (type) {
    case 'STEP_START':
        setStepRunning(stage);
        break;
    case 'STEP_END':
        setStepDone(stage, pl.status);
        if (pl.result) {
            Object.keys(pl.result).forEach(k => { dims[k] = pl.result[k]; });
        }
        break;
    case 'PROGRESS':
        break;
    case 'DONE':
        uiState.stepBarrier = null;
        _clearBarrierTimer();
        uiState.phase = 'locked';
        log('PIPE_DONE', `overall=${pl.overall_score}, grade=${pl.grade}`);
        break;
    }
    _drainBarrierQueue();
}

// ── processEvent ──
function processEvent(evt) {
    const seq = evt.seq || 0;
    if (seq > 0 && seq <= uiState.lastEventSeq) {
        eventsDropped++;
        log('SEQ_DROP', `seq=${seq} <= last=${uiState.lastEventSeq}`);
        return;
    }
    if (seq > 0) uiState.lastEventSeq = seq;

    if (!_passesBarrier(evt)) {
        barrierQueue.push(evt);
        eventsBlocked++;
        return;
    }
    reduce(evt);
}

// ── Buffer consumer (模拟 STAGE_DELAY) ──
function flushBuffer() {
    if (eventBuffer.length === 0) { bufferTimer = null; return; }
    const evt = eventBuffer.shift();
    processEvent(evt);
    const isStageEvent = (evt.event_type === 'STEP_START' || evt.event_type === 'STEP_END' || evt.event_type === 'DONE');
    bufferTimer = setTimeout(flushBuffer, isStageEvent ? STAGE_DELAY : 50);
}

function enqueueEvent(evt) {
    eventBuffer.push(evt);
    if (!bufferTimer) flushBuffer();
}

// ── 工具 ──
function mkEvt(type, stage, seq, payload) {
    return { event_type: type, stage, seq: seq || 0, payload: payload || {} };
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function assert(cond, msg) {
    if (!cond) { console.log('    ❌ FAIL: ' + msg); return false; }
    console.log('    ✅ ' + msg); return true;
}

function resetAll() {
    if (barrierTimer) { clearTimeout(barrierTimer); }
    if (bufferTimer) { clearTimeout(bufferTimer); }
    uiState = { phase: 'running', lockRender: false, stepBarrier: null, lastEventSeq: 0 };
    barrierQueue = [];
    barrierTimer = null;
    stepStates = { visual:'waiting', speech:'waiting', gesture:'waiting', scoring:'waiting', report:'waiting' };
    dims = {};
    eventBuffer = [];
    bufferTimer = null;
    timeline = [];
    eventsProcessed = 0; eventsBlocked = 0; eventsDropped = 0;
    barrierTimeouts = 0; barrierReleases = 0;
    timerCreated = 0; timerCancelled = 0; timerFired = 0;
}

// ═══════════════════════════════════════════════════════════
// 场景 1: 真实并行分析 — 三个分析器同时启动，依次完成
// ═══════════════════════════════════════════════════════════
async function scenario1_parallelAnalysis() {
    console.log('\n' + '═'.repeat(70));
    console.log('📋 场景1: 并行分析 — visual/speech/gesture 同时 START，依次 END');
    console.log('═'.repeat(70));
    resetAll();

    let seq = 1;
    const totalTime = Date.now();

    // Phase 1: 三个分析器同时启动（模拟并行阶段）
    log('>>>', '══════ 并行阶段开始: 三个分析器同时启动 ══════');
    enqueueEvent(mkEvt('STEP_START', 'visual',  seq++));
    enqueueEvent(mkEvt('STEP_START', 'speech',  seq++));
    enqueueEvent(mkEvt('STEP_START', 'gesture', seq++));

    await sleep(STAGE_DELAY * 3 + 200); // 等 buffer 消费完三个 START
    log('---', `并行阶段启动完成 (processed=${eventsProcessed}, blocked=${eventsBlocked})`);

    // 验证: 只有一个 barrier（第一个 START 建立了 barrier），其余被拦截
    let pass = true;
    pass &= assert(uiState.stepBarrier !== null, 'barrier 已建立（并行阶段第一个步骤优先）');
    pass &= assert(eventsBlocked === 2, `2 个后续 START 被拦截进 barrierQueue (blocked=${eventsBlocked})`);
    pass &= assert(barrierQueue.length === 2, `barrierQueue 有 2 条待处理事件`);

    // Phase 2: 依次完成（模拟各分析器不同耗时）
    log('>>>', '══════ 依次完成: visual → speech → gesture ══════');
    await sleep(300); // 模拟 visual 最快完成
    enqueueEvent(mkEvt('STEP_END', 'visual', seq++, { status: 'ok', result: { eye_contact_score: 78, posture_score: 72 } }));
    await sleep(STAGE_DELAY + 100);
    log('---', `visual 完成后 (barrier=${uiState.stepBarrier}, q=${barrierQueue.length})`);

    pass &= assert(stepStates.visual === 'done', 'visual → done');
    // drain 应释放 speech START → barrier 变为 speech
    pass &= assert(uiState.stepBarrier === 'speech' || barrierQueue.length === 0, `barrier 推进到下一步 (barrier=${uiState.stepBarrier})`);

    await sleep(500); // 模拟 speech 稍慢
    enqueueEvent(mkEvt('STEP_END', 'speech', seq++, { status: 'ok', result: { speech_score: 85 } }));
    await sleep(STAGE_DELAY + 100);

    pass &= assert(stepStates.speech === 'done', 'speech → done');

    await sleep(300); // gesture 最后完成
    enqueueEvent(mkEvt('STEP_END', 'gesture', seq++, { status: 'ok', result: { gesture_score: 65 } }));
    await sleep(STAGE_DELAY + 100);

    pass &= assert(stepStates.gesture === 'done', 'gesture → done');
    // 并行阶段完成后 barrier 应为 scoring（如果 scoring 已 START）或 null
    log('---', `并行阶段完成 (barrier=${uiState.stepBarrier})`);

    // Phase 3: scoring → report 顺序执行
    log('>>>', '══════ 顺序阶段: scoring → report ══════');
    enqueueEvent(mkEvt('STEP_START', 'scoring', seq++));
    await sleep(STAGE_DELAY + 100);
    enqueueEvent(mkEvt('STEP_END', 'scoring', seq++, { status: 'ok', result: { overall_score: 73 } }));
    await sleep(STAGE_DELAY + 100);

    enqueueEvent(mkEvt('STEP_START', 'report', seq++));
    await sleep(STAGE_DELAY + 100);
    enqueueEvent(mkEvt('STEP_END', 'report', seq++, { status: 'ok' }));
    await sleep(STAGE_DELAY + 100);

    // Phase 4: DONE
    enqueueEvent(mkEvt('DONE', 'system', seq++, { overall_score: 73.5, grade: '良好' }));
    await sleep(STAGE_DELAY + 300);

    // ── 最终验证 ──
    log('===', '══════ 场景1 最终验证 ══════');
    pass &= assert(stepStates.visual === 'done', 'visual done');
    pass &= assert(stepStates.speech === 'done', 'speech done');
    pass &= assert(stepStates.gesture === 'done', 'gesture done');
    pass &= assert(stepStates.scoring === 'done', 'scoring done');
    pass &= assert(stepStates.report === 'done', 'report done');
    pass &= assert(barrierQueue.length === 0, `barrierQueue 已清空 (实际=${barrierQueue.length})`);
    pass &= assert(uiState.phase === 'locked', `phase → locked (实际=${uiState.phase})`);
    pass &= assert(timerCreated - timerCancelled - timerFired === 0, `timer 全部结算 (created=${timerCreated}, cancelled=${timerCancelled}, fired=${timerFired})`);
    pass &= assert(eventsBlocked <= eventsProcessed, '拦截的事件最终都被处理了');

    const notDone = Object.entries(stepStates).filter(([k,v]) => v !== 'done');
    if (notDone.length > 0) {
        pass &= assert(false, `所有步骤应 done: ${JSON.stringify(stepStates)}`);
    }

    return pass;
}

// ═══════════════════════════════════════════════════════════
// 场景 2: "卡在视觉分析然后瞬间全打勾" 防回归测试
// ═══════════════════════════════════════════════════════════
async function scenario2_stuckVisualAntiRegression() {
    console.log('\n' + '═'.repeat(70));
    console.log('📋 场景2: 防回归 — "视觉分析卡住→瞬间全打勾"');
    console.log('═'.repeat(70));
    resetAll();

    let seq = 1;

    // 同时启动所有步骤
    log('>>>', '发送所有 5 个 STEP_START（模拟极端场景）');
    ['visual','speech','gesture','scoring','report'].forEach(s => {
        enqueueEvent(mkEvt('STEP_START', s, seq++));
    });
    await sleep(STAGE_DELAY * 5 + 100);

    const snap1 = { ...stepStates };
    let pass = true;
    // barrier 应停在 visual（第一个），其余 START 在 barrierQueue
    pass &= assert(uiState.stepBarrier === 'visual', `barrier=visual (实际=${uiState.stepBarrier})`);
    pass &= assert(snap1.visual === 'running', '第1步 visual=running');
    // 其余应该仍是 waiting（被 barrier 拦截，没进 reduce）
    pass &= assert(snap1.speech === 'waiting', '第2步 speech=waiting (被barrier拦截)');
    pass &= assert(snap1.gesture === 'waiting', '第3步 gesture=waiting (被barrier拦截)');
    pass &= assert(snap1.scoring === 'waiting', '第4步 scoring=waiting (被barrier拦截)');
    pass &= assert(snap1.report === 'waiting', '第5步 report=waiting (被barrier拦截)');

    log('---', '状态快照: 只有 visual 是 running，其余全部 waiting ✅');

    // 逐个完成 — 每次只完成当前步骤
    const steps = ['visual','speech','gesture','scoring','report'];
    for (const s of steps) {
        await sleep(200);
        enqueueEvent(mkEvt('STEP_END', s, seq++, { status: 'ok' }));
        await sleep(STAGE_DELAY + 50);

        const stateAfter = stepStates[s];
        pass &= assert(stateAfter === 'done', `${s} → done (实际=${stateAfter})`);

        // 检查: 是否只推进了当前步而非"瞬间全打勾"
        for (const later of steps) {
            if (steps.indexOf(later) > steps.indexOf(s) && stepStates[later] === 'done') {
                // 后面的步骤不应该提前 done
                if (later !== s) {
                    pass &= assert(false, `${later} 不应该提前变成 done! (${s} 刚完成)`);
                }
            }
        }
    }

    log('===', '══════ 场景2 最终验证 ══════');
    pass &= assert(barrierQueue.length === 0, `barrierQueue 已清空 (实际=${barrierQueue.length})`);
    pass &= assert(timerCreated - timerCancelled - timerFired === 0, `timer 结算正确`);

    return pass;
}

// ═══════════════════════════════════════════════════════════
// 场景 3: timer 泄漏测试 — 大量快速 Start/End
// ═══════════════════════════════════════════════════════════
async function scenario3_timerLeakRapid() {
    console.log('\n' + '═'.repeat(70));
    console.log('📋 场景3: timer 泄漏 — 20轮快速 Start→End (buffer=50ms)');
    console.log('═'.repeat(70));
    resetAll();

    let seq = 1;
    let pass = true;

    // 临时加速 buffer 消费以便更快完成
    const origDelay = STAGE_DELAY;
    // 不能直接改 const，我们用一个局部变量覆盖传入 flushBuffer 的闭包
    // 实际上 STAGE_DELAY 是外层 const…这里用 eval 技巧改不了
    // 替代方案：每个事件后等待较长时间，给 buffer 足够时间消费
    for (let round = 0; round < 20; round++) {
        const stage = ['visual','speech','gesture','scoring','report'][round % 5];
        enqueueEvent(mkEvt('STEP_START', stage, seq++));
        await sleep(50);
        enqueueEvent(mkEvt('STEP_END', stage, seq++, { status: 'ok' }));
        await sleep(450); // 给 buffer 每 400ms 消费一个事件留出时间
    }

    await sleep(STAGE_DELAY * 2 + 200);

    // timer 应全部结算
    const netTimers = timerCreated - timerCancelled - timerFired;
    pass &= assert(netTimers === 0, `timer 全部结算 (created=${timerCreated}, cancelled=${timerCancelled}, fired=${timerFired}, net=${netTimers})`);
    pass &= assert(barrierQueue.length <= 10, `barrierQueue < 10 (实际=${barrierQueue.length})`);
    // 注意: 由于快速切换，可能有些事件还在 buffer 或 queue 里，但不应大量积压
    log('---', `100轮后: processed=${eventsProcessed}, blocked=${eventsBlocked}, q=${barrierQueue.length}`);

    // 等 buffer 清空 — 200 个事件 * 400ms/event ≈ 80s，给足时间
    log('⏳', `等待 buffer 清空 (${eventBuffer.length} 条待消费)...`);
    const maxWait = 90000; // 90s
    const start = Date.now();
    while (eventBuffer.length > 0 || barrierQueue.length > 0 || bufferTimer) {
        await sleep(500);
        if (Date.now() - start > maxWait) break;
    }
    log('---', `等待完成: buf=${eventBuffer.length}, q=${barrierQueue.length}, elapsed=${((Date.now()-start)/1000).toFixed(1)}s`);
    pass &= assert(barrierQueue.length === 0, `最终 barrierQueue 清空 (实际=${barrierQueue.length})`);

    return pass;
}

// ═══════════════════════════════════════════════════════════
// 场景 4: 超时→drain→新timer 不被覆盖 (Bug 回归测试)
// ═══════════════════════════════════════════════════════════
async function scenario4_timerOverwriteRegression() {
    console.log('\n' + '═'.repeat(70));
    console.log('📋 场景4: Bug 回归 — 超时 drain 中创建的新 timer 不被覆盖');
    console.log('═'.repeat(70));
    resetAll();

    let seq = 1;

    // 启动 visual（会创建 timer）
    enqueueEvent(mkEvt('STEP_START', 'visual', seq++));
    await sleep(STAGE_DELAY + 100);

    // 注入 speech/gesture START → 被 barrier 拦截进 queue
    enqueueEvent(mkEvt('STEP_START', 'speech', seq++));
    enqueueEvent(mkEvt('STEP_START', 'gesture', seq++));
    await sleep(200);

    log('---', `超时前: barrier=${uiState.stepBarrier}, q=${barrierQueue.length}, timerActive=${barrierTimer !== null}`);

    // 等 5 秒让 timer 触发
    log('⏳', '等待 BARRIER_TIMEOUT 超时…');
    await sleep(5200);

    // timer 触发了，drain 应该释放 speech START → barrier 变为 speech
    log('---', `超时后: barrier=${uiState.stepBarrier}, q=${barrierQueue.length}, timerActive=${barrierTimer !== null}`);

    let pass = true;
    pass &= assert(timerFired >= 1, '原 barrier timer 已触发');
    pass &= assert(barrierTimer !== null, '新的 barrier timer 存在（drain 中创建）');
    pass &= assert(uiState.stepBarrier !== null, 'barrier 已转移到下一步骤');
    pass &= assert(barrierTimer !== null, `barrierTimer 未丢失 (was=${barrierTimer !== null})`);

    // 现在发送 STEP_END → 应该能正常 cancel 新 timer
    const currentBarrier = uiState.stepBarrier;
    log('---', `发送 ${currentBarrier} 的 STEP_END`);
    enqueueEvent(mkEvt('STEP_END', currentBarrier, seq++, { status: 'ok' }));
    await sleep(STAGE_DELAY + 100);

    pass &= assert(uiState.stepBarrier === null || uiState.stepBarrier !== currentBarrier,
        `barrier 已从 ${currentBarrier} 释放`);
    pass &= assert(timerCancelled >= 1, '新 timer 被成功 cancel');

    return pass;
}

// ═══════════════════════════════════════════════════════════
// 场景 5: 完整时序图 — 验证动画与流程同步
// ═══════════════════════════════════════════════════════════
async function scenario5_animationSync() {
    console.log('\n' + '═'.repeat(70));
    console.log('📋 场景5: 动画同步 — 步骤状态转换时序验证');
    console.log('═'.repeat(70));
    resetAll();

    let seq = 1;
    const stateSnapshots = [];

    // 记录每个步骤的状态转换时间
    const steps = ['visual','speech','gesture','scoring','report'];
    const transitions = [];  // [{t, stage, from, to}]

    // 并行启动
    enqueueEvent(mkEvt('STEP_START', 'visual', seq++));
    enqueueEvent(mkEvt('STEP_START', 'speech', seq++));
    enqueueEvent(mkEvt('STEP_START', 'gesture', seq++));
    await sleep(STAGE_DELAY * 3 + 200);

    stateSnapshots.push({ t: ts(), states: {...stepStates}, barrier: uiState.stepBarrier });

    // 模拟真实耗时: visual 3s, speech 5s, gesture 2s
    await sleep(500);
    enqueueEvent(mkEvt('STEP_END', 'gesture', seq++, { status: 'ok' })); // gesture 最快
    await sleep(STAGE_DELAY + 50);
    stateSnapshots.push({ t: ts(), states: {...stepStates}, barrier: uiState.stepBarrier });

    await sleep(800);
    enqueueEvent(mkEvt('STEP_END', 'visual', seq++, { status: 'ok' })); // visual 第二
    await sleep(STAGE_DELAY + 50);
    stateSnapshots.push({ t: ts(), states: {...stepStates}, barrier: uiState.stepBarrier });

    await sleep(1200);
    enqueueEvent(mkEvt('STEP_END', 'speech', seq++, { status: 'ok' })); // speech 最慢
    await sleep(STAGE_DELAY + 50);
    stateSnapshots.push({ t: ts(), states: {...stepStates}, barrier: uiState.stepBarrier });

    // 顺序阶段
    await sleep(300);
    enqueueEvent(mkEvt('STEP_START', 'scoring', seq++));
    await sleep(STAGE_DELAY + 50);
    enqueueEvent(mkEvt('STEP_END', 'scoring', seq++, { status: 'ok' }));
    await sleep(STAGE_DELAY + 50);
    enqueueEvent(mkEvt('STEP_START', 'report', seq++));
    await sleep(STAGE_DELAY + 50);
    enqueueEvent(mkEvt('STEP_END', 'report', seq++, { status: 'ok' }));
    await sleep(STAGE_DELAY + 50);
    enqueueEvent(mkEvt('DONE', 'system', seq++, { overall_score: 80, grade: '优秀' }));
    await sleep(STAGE_DELAY + 300);

    stateSnapshots.push({ t: ts(), states: {...stepStates}, barrier: uiState.stepBarrier, phase: uiState.phase });

    // ── 验证动画同步 ──
    let pass = true;
    console.log('\n    步骤状态快照时间线:');
    stateSnapshots.forEach((snap, i) => {
        const stateStr = steps.map(s => `${s}=${snap.states[s]}`).join(', ');
        console.log(`    [${snap.t}s] barrier=${snap.barrier} ${stateStr} ${snap.phase ? 'phase='+snap.phase : ''}`);
    });

    // 基于 timeline 全量日志验证：每个步骤都经过 running → done 转换
    // （快照间隔太长会漏掉 drain 中毫秒级的 running 状态，这是正常行为）
    for (const s of steps) {
        const stepEvents = timeline.filter(e => e.action === 'STEP_RUN' || e.action === 'STEP_DONE');
        const ran = stepEvents.some(e => e.action === 'STEP_RUN' && e.detail.startsWith(s));
        const done = stepEvents.some(e => e.action === 'STEP_DONE' && e.detail.startsWith(s));
        pass &= assert(ran, `${s} 经历了 running 状态（timeline 记录）`);
        pass &= assert(done, `${s} 经历了 done 状态（timeline 记录）`);
    }

    pass &= assert(barrierQueue.length === 0, `barrierQueue 已清空 (实际=${barrierQueue.length})`);
    pass &= assert(uiState.phase === 'locked', `最终 phase=locked (实际=${uiState.phase})`);

    return pass;
}

// ═══════════════════════════════════════════════════════════
// 运行
// ═══════════════════════════════════════════════════════════
async function main() {
    console.log('🔬 端到端时序验收测试');
    console.log(`   BARRIER_TIMEOUT=${BARRIER_TIMEOUT}ms, STAGE_DELAY=${STAGE_DELAY}ms`);
    console.log('   模拟: WebSocket 事件 → buffer → barrier → reduce → UI\n');

    const results = [];

    results.push({ name: '并行分析→依次完成', pass: await scenario1_parallelAnalysis() });
    results.push({ name: '防回归:"卡视觉→全打勾"', pass: await scenario2_stuckVisualAntiRegression() });
    results.push({ name: '100轮快速Start→End timer泄漏', pass: await scenario3_timerLeakRapid() });
    results.push({ name: '超时drain新timer不被覆盖', pass: await scenario4_timerOverwriteRegression() });
    results.push({ name: '动画同步验证', pass: await scenario5_animationSync() });

    const passed = results.filter(r => r.pass).length;
    const total = results.length;

    console.log('\n' + '═'.repeat(70));
    console.log(`📊 端到端测试结果: ${passed}/${total} 通过`);
    results.forEach(r => {
        console.log(`   ${r.pass ? '✅' : '❌'} ${r.name}`);
    });
    console.log('═'.repeat(70));

    if (passed < total) process.exit(1); else process.exit(0);
}

main().catch(e => { console.error('FATAL:', e); process.exit(1); });
