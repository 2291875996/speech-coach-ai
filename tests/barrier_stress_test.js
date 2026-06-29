/**
 * barrierQueue 压力测试 — 纯逻辑模拟
 * 提取 app.js 中 barrier 状态机的核心逻辑，注入模拟事件，验证行为正确性。
 * 运行: node tests/barrier_stress_test.js
 */
'use strict';

// ═══════════════════════════════════════════════════════════════
// 0. 模拟环境（与 app.js 一致的状态机核心）
// ═══════════════════════════════════════════════════════════════
const BARRIER_TIMEOUT = 5000;
const BARRIER_QUEUE_MAX = 100;
const STAGE_DELAY = 400;

let uiState = {
    phase: 'running',       // idle | running | locked | done
    lockRender: false,
    stepBarrier: null,
    lastEventSeq: 0
};
let barrierQueue = [];
let barrierTimer = null;
let stepStates = {};
let dims = {};

// 计数器
let eventsProcessed = 0;
let eventsBlocked = 0;
let eventsDroppedBySeq = 0;
let eventsDroppedByOverflow = 0;
let barrierTimeoutsFired = 0;
let barrierNormalReleases = 0;
let drainCalls = 0;

function resetState() {
    // 先释放 barrier 再清队列，防止 drain 中产生新 timer
    uiState.stepBarrier = null;
    if (barrierTimer) { clearTimeout(barrierTimer); barrierTimer = null; }
    barrierQueue = [];
    uiState = { phase: 'running', lockRender: false, stepBarrier: null, lastEventSeq: 0 };
    stepStates = { visual: 'waiting', speech: 'waiting', gesture: 'waiting', scoring: 'waiting', report: 'waiting' };
    dims = {};
    eventsProcessed = 0;
    eventsBlocked = 0;
    eventsDroppedBySeq = 0;
    eventsDroppedByOverflow = 0;
    barrierTimeoutsFired = 0;
    barrierNormalReleases = 0;
    drainCalls = 0;
}

// ── barrier timer ──
let _timerIdCounter = 0;
let _activeTimerIds = new Set();

function _startBarrierTimer(stage) {
    _clearBarrierTimer();
    const id = ++_timerIdCounter;
    _activeTimerIds.add(id);
    barrierTimer = setTimeout(() => {
        _activeTimerIds.delete(id);
        barrierTimeoutsFired++;
        console.log(`  ⏰ [BARRIER] 超时! stage=${stage}, queue=${barrierQueue.length}条 (timerId=${id})`);
        uiState.stepBarrier = null;
        _drainBarrierQueue();
        // 仅当 drain 未重新建立 barrier 时才清空引用
        if (uiState.stepBarrier === null) {
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

// ── barrier 检查 ──
function _passesBarrier(evt) {
    const ut = uiState;
    if (ut.phase !== 'running') return false;
    if (ut.lockRender) return false;
    const sysTypes = ['SYSTEM_INIT', 'LOG', 'ERROR', 'HEARTBEAT', 'STREAM_END', 'STATUS'];
    if (sysTypes.indexOf(evt.event_type) >= 0) return true;
    if (ut.stepBarrier !== null && evt.stage !== ut.stepBarrier) return false;
    return true;
}

// ── drain ──
function _drainBarrierQueue() {
    drainCalls++;
    if (barrierQueue.length > BARRIER_QUEUE_MAX) {
        const overflow = barrierQueue.length - BARRIER_QUEUE_MAX;
        eventsDroppedByOverflow += overflow;
        console.log(`  ⚠️ [BARRIER] 溢出: 丢弃${overflow}条 (共${barrierQueue.length}条)`);
        barrierQueue = barrierQueue.slice(-BARRIER_QUEUE_MAX);
    }
    while (barrierQueue.length > 0) {
        const evt = barrierQueue.shift();
        if (_passesBarrier(evt)) {
            reduce(evt);
        } else {
            barrierQueue.unshift(evt);
            break;
        }
    }
}

// ── 步骤状态 ──
function setStepRunning(stage) {
    if (stepStates[stage] !== 'running') {
        stepStates[stage] = 'running';
        if (uiState.stepBarrier === null) {
            uiState.stepBarrier = stage;
            _startBarrierTimer(stage);
        }
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
            barrierNormalReleases++;
        }
        return true;
    }
    return false;
}

// ── reduce ──
function reduce(evt) {
    const norm = evt.event_type;
    const stage = evt.stage || '';
    const pl = evt.payload || {};

    switch (norm) {
    case 'STEP_START':
        setStepRunning(stage);
        break;
    case 'STEP_END':
        setStepDone(stage, pl.status);
        break;
    case 'PROGRESS':
        break; // no state change needed for test
    case 'DONE':
        uiState.stepBarrier = null;
        _clearBarrierTimer();
        uiState.phase = 'locked';
        break;
    }
    eventsProcessed++;
    _drainBarrierQueue();
}

// ── processEvent ──
function processEvent(evt) {
    const seq = evt.seq || 0;
    if (seq > 0 && seq <= uiState.lastEventSeq) {
        eventsDroppedBySeq++;
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

// ── 工具 ──
function makeEvent(type, stage, seq, payload) {
    return { event_type: type, stage: stage, seq: seq || 0, payload: payload || {}, cost: 0 };
}

function status() {
    return {
        phase: uiState.phase,
        stepBarrier: uiState.stepBarrier,
        lockRender: uiState.lockRender,
        lastSeq: uiState.lastEventSeq,
        stepStates: { ...stepStates },
        barrierQueueLen: barrierQueue.length,
        timerActive: barrierTimer !== null,
        eventsProcessed,
        eventsBlocked,
        eventsDroppedBySeq,
        eventsDroppedByOverflow,
        barrierTimeoutsFired,
        barrierNormalReleases,
        drainCalls
    };
}

function assert(cond, msg) {
    if (!cond) { console.log('  ❌ FAIL: ' + msg); return false; }
    console.log('  ✅ ' + msg);
    return true;
}

// ═══════════════════════════════════════════════════════════════
// 测试 1: 长耗时 — barrier 超时保护
// ═══════════════════════════════════════════════════════════════
async function test1_longRunning() {
    console.log('\n━━━ 测试1: 长耗时语音分析 (barrier 超过 BARRIER_TIMEOUT) ━━━');
    resetState();

    // send STEP_START speech
    processEvent(makeEvent('STEP_START', 'speech', 1));
    console.log('  发送 STEP_START speech → barrier=' + uiState.stepBarrier);

    // send PROGRESS (passes barrier)
    processEvent(makeEvent('PROGRESS', 'speech', 2, { percent: 50 }));
    console.log('  发送 PROGRESS speech → processed=' + eventsProcessed);

    // send other stages (blocked by barrier)
    processEvent(makeEvent('STEP_START', 'gesture', 3));
    processEvent(makeEvent('STEP_START', 'scoring', 4));
    console.log('  发送 gesture/scoring → blocked=' + eventsBlocked + ' queue=' + barrierQueue.length);

    // wait for timeout
    console.log('  ⏳ 等待超时 (6s)…');
    await sleep(6000);

    const s = status();
    let pass = true;
    pass &= assert(s.barrierTimeoutsFired >= 1, '原 barrier 已由超时释放');
    // 超时释放后 drain 了队列中 gesture/scoring 的 STEP_START，
    // barrier 正确转移到下一步骤（非 null），这正是 pipeline 继续推进的正确行为
    pass &= assert(s.stepBarrier !== 'speech', 'barrier 不再是原 stage (speech)');
    pass &= assert(s.stepStates.speech === 'running', 'speech 保持 running（无 STEP_END）');
    pass &= assert(s.phase === 'running', 'phase 仍为 running（无 DONE 事件）');
    return pass;
}

// ═══════════════════════════════════════════════════════════════
// 测试 2: 连续快速 Start→End 的 5 个步骤
// ═══════════════════════════════════════════════════════════════
async function test2_rapidCycle() {
    console.log('\n━━━ 测试2: 5个步骤快速 Start→End 循环 ━━━');
    resetState();

    const stages = ['visual', 'speech', 'gesture', 'scoring', 'report'];
    let pass = true;
    let seq = 1;

    for (const stage of stages) {
        processEvent(makeEvent('STEP_START', stage, seq++));
        await sleep(50);
        pass &= assert(uiState.stepBarrier === stage, `barrier 设为 "${stage}"`);

        processEvent(makeEvent('STEP_END', stage, seq++, { status: 'ok' }));
        await sleep(50);
        pass &= assert(uiState.stepBarrier === null, `barrier 已释放 (${stage})`);
    }

    pass &= assert(barrierNormalReleases === 5, '5 次正常释放');
    pass &= assert(barrierTimeoutsFired === 0, '0 次超时（全部正常）');
    pass &= assert(eventsBlocked === 0, '0 事件被拦截（无并发冲突）');
    return pass;
}

// ═══════════════════════════════════════════════════════════════
// 测试 3: WS 断连后 drain barrierQueue
// ═══════════════════════════════════════════════════════════════
async function test3_disconnect() {
    console.log('\n━━━ 测试3: WS 断连 — 强制 drain barrierQueue ━━━');
    resetState();

    // barrier = visual
    processEvent(makeEvent('STEP_START', 'visual', 1));
    await sleep(50);

    // 其他 stage 被拦截
    processEvent(makeEvent('STEP_START', 'speech', 2));
    processEvent(makeEvent('STEP_START', 'gesture', 3));
    processEvent(makeEvent('PROGRESS', 'gesture', 4, { percent: 30 }));
    processEvent(makeEvent('STEP_START', 'scoring', 5));
    console.log(`  被拦截事件: ${eventsBlocked} 条, queue=${barrierQueue.length}`);

    // 模拟 WS onclose: 直接 drain barrierQueue
    console.log('  🔌 模拟 WS 断连，直接 drain…');
    let drained = 0;
    while (barrierQueue.length > 0) {
        const evt = barrierQueue.shift();
        reduce(evt);
        drained++;
    }
    console.log(`  强制 drain 了 ${drained} 条事件`);

    let pass = true;
    // barrier 应该被 drain 中的事件链正常推进
    // speech 的 STEP_START → barrier=speech, speech STEP_END 未发送
    // 所以 barrier 可能在某个中间状态
    pass &= assert(drained === 4, '4 条拦截事件全部处理');
    pass &= assert(eventsProcessed >= 5, '至少处理了 5 个事件');
    return pass;
}

// ═══════════════════════════════════════════════════════════════
// 测试 4: 队列溢出上限 (注入 150 条)
// ═══════════════════════════════════════════════════════════════
async function test4_overflow() {
    console.log('\n━━━ 测试4: 队列超过 BARRIER_QUEUE_MAX (100) ━━━');
    resetState();

    // barrier = visual
    processEvent(makeEvent('STEP_START', 'visual', 1));
    await sleep(50);

    // 注入 150 条非匹配事件
    console.log('  📥 注入 150 条 gesture/scoring/report 事件…');
    for (let i = 0; i < 150; i++) {
        const stage = ['gesture', 'scoring', 'report'][i % 3];
        const type = i % 2 === 0 ? 'STEP_START' : 'PROGRESS';
        processEvent(makeEvent(type, stage, 100 + i));
    }

    const sBefore = status();
    console.log(`  注入后: queue=${sBefore.barrierQueueLen}, blocked=${eventsBlocked}, overflow=${eventsDroppedByOverflow}`);

    // 手动释放 barrier 然后 drain
    uiState.stepBarrier = null;
    _clearBarrierTimer();
    _drainBarrierQueue();

    await sleep(100);

    const sAfter = status();
    let pass = true;
    pass &= assert(sAfter.eventsDroppedByOverflow > 0, '溢出丢弃 > 0 条');
    pass &= assert(sAfter.barrierQueueLen <= BARRIER_QUEUE_MAX, `drain 后队列 <= ${BARRIER_QUEUE_MAX}`);
    pass &= assert(sAfter.eventsDroppedByOverflow <= 50, `最多丢弃 50 条（150-100）`);
    return pass;
}

// ═══════════════════════════════════════════════════════════════
// 测试 5: seq 乱序 + barrier 并发
// ═══════════════════════════════════════════════════════════════
async function test5_seqOrdering() {
    console.log('\n━━━ 测试5: seq 乱序检查 ━━━');
    resetState();

    // 正常序列
    processEvent(makeEvent('STEP_START', 'visual', 10));
    processEvent(makeEvent('STEP_END', 'visual', 11, { status: 'ok' }));
    processEvent(makeEvent('STEP_START', 'speech', 12));

    // 过期事件
    processEvent(makeEvent('PROGRESS', 'visual', 5));  // seq=5 < last=12
    processEvent(makeEvent('STEP_END', 'visual', 9));   // seq=9 < last=12

    let pass = true;
    pass &= assert(eventsDroppedBySeq === 2, '2 条过期事件被丢弃');
    pass &= assert(eventsProcessed === 3, '3 条正常事件被处理');
    return pass;
}

// ═══════════════════════════════════════════════════════════════
// 测试 6: 超时 + DONE 竞态
// ═══════════════════════════════════════════════════════════════
async function test6_timeoutVsDone() {
    console.log('\n━━━ 测试6: barrier 超时 vs DONE 竞态 ━━━');
    resetState();

    // barrier = speech, no STEP_END
    processEvent(makeEvent('STEP_START', 'speech', 1));
    console.log('  设置 barrier=speech, 无 STEP_END');

    await sleep(5100); // 等超时

    let pass = true;
    pass &= assert(barrierTimeoutsFired === 1, '超时触发');
    pass &= assert(uiState.stepBarrier === null, 'barrier 已释放');

    // 超时后发 DONE — 不应冲突
    processEvent(makeEvent('DONE', 'system', 100, { overall_score: 75, grade: '良好' }));
    await sleep(50);

    pass &= assert(barrierTimeoutsFired === 1, 'DONE 未触发新超时');
    pass &= assert(uiState.phase === 'locked', 'phase 进入 locked');
    return pass;
}

// ═══════════════════════════════════════════════════════════════
// 运行
// ═══════════════════════════════════════════════════════════════
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function main() {
    console.log('🔬 barrierQueue 压力测试 — 纯逻辑模拟');
    console.log(`   BARRIER_TIMEOUT=${BARRIER_TIMEOUT}ms, BARRIER_QUEUE_MAX=${BARRIER_QUEUE_MAX}\n`);

    const results = [];
    results.push(await test1_longRunning());
    results.push(await test2_rapidCycle());
    results.push(await test3_disconnect());
    results.push(await test4_overflow());
    results.push(await test5_seqOrdering());
    results.push(await test6_timeoutVsDone());

    const passed = results.filter(Boolean).length;
    const total = results.length;

    console.log('\n' + '═'.repeat(60));
    console.log(`📊 测试结果: ${passed}/${total} 通过`);
    console.log('═'.repeat(60));

    if (passed < total) {
        const names = ['长耗时超时', '快速Start→End', 'WS断连drain', '队列溢出', 'seq乱序', '超时vsDONE竞态'];
        names.forEach((n, i) => {
            if (!results[i]) console.log(`  ❌ ${n}`);
        });
        process.exit(1);
    } else {
        console.log('  ✅ 全部通过');
        process.exit(0);
    }
}

main().catch(e => { console.error(e); process.exit(1); });
