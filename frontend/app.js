/**
 * app.js — Anti-Jamming Dashboard
 * Handles: JWT auth guard, SSE streaming, Chart.js charts,
 * live telemetry polling, WebSocket, jamming controls.
 */

// ─── Auth Guard ────────────────────────────────────────────────────────────
const TOKEN = localStorage.getItem('aj_token');
const USERNAME = localStorage.getItem('aj_username') || 'admin';

if (!TOKEN) {
    window.location.href = '/';
}

document.getElementById('logout-btn')?.addEventListener('click', () => {
    localStorage.removeItem('aj_token');
    localStorage.removeItem('aj_username');
    window.location.href = '/';
});

// ─── Authenticated fetch helper ────────────────────────────────────────────
async function apiFetch(url, options = {}) {
    const res = await fetch(url, {
        ...options,
        headers: {
            'Authorization': `Bearer ${TOKEN}`,
            'Content-Type': 'application/json',
            ...(options.headers || {}),
        },
    });
    if (res.status === 401) {
        localStorage.removeItem('aj_token');
        window.location.href = '/';
    }
    return res;
}

// ─── Utility ───────────────────────────────────────────────────────────────
const fmt = (n, d = 2) => (n == null ? '—' : Number(n).toFixed(d));

function logEvent(msg, type = 'info') {
    const el = document.getElementById('event-log');
    const now = new Date().toLocaleTimeString();
    const div = document.createElement('div');
    div.className = `log-entry log-${type}`;
    div.innerHTML = `<span class="log-time">${now}</span>${msg}`;
    el.prepend(div);
    // Keep max 50 entries
    while (el.children.length > 50) el.lastChild.remove();
}

// ─── State ─────────────────────────────────────────────────────────────────
let selectedDrone = 'drone_1';

// ─── Uptime ────────────────────────────────────────────────────────────────
const startTime = Date.now();
setInterval(() => {
    const s = Math.floor((Date.now() - startTime) / 1000);
    const m = Math.floor(s / 60);
    document.getElementById('uptime').textContent = m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}, 1000);

// ─── Chart.js Setup ────────────────────────────────────────────────────────
const CHART_MAX_POINTS = 60;

const chartDefaults = {
    type: 'line',
    options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 200 },
        plugins: { legend: { display: false } },
        scales: {
            x: {
                ticks: { color: '#3d5280', font: { size: 9 }, maxRotation: 0 },
                grid: { color: 'rgba(99,145,255,0.05)' },
            },
            y: {
                ticks: { color: '#7890b8', font: { size: 10 } },
                grid: { color: 'rgba(99,145,255,0.08)' },
            },
        },
    },
};

function makeChart(id, label, color, yMin = null, yMax = null) {
    const ctx = document.getElementById(id).getContext('2d');
    const opts = JSON.parse(JSON.stringify(chartDefaults));
    if (yMin !== null) opts.options.scales.y.min = yMin;
    if (yMax !== null) opts.options.scales.y.max = yMax;
    return new Chart(ctx, {
        ...opts,
        data: {
            labels: [],
            datasets: [{
                label,
                data: [],
                borderColor: color,
                backgroundColor: color.replace(')', ', 0.08)').replace('rgb', 'rgba'),
                borderWidth: 2,
                pointRadius: 0,
                fill: true,
                tension: 0.4,
            }],
        },
    });
}

const rewardChart = makeChart('chart-reward', 'RL Reward', 'rgb(79,142,255)', -1, 1.2);
const threatChart = makeChart('chart-threat', 'Threat Score (Max Path)', 'rgb(239,68,68)', 0, 1.1);

function pushToChart(chart, label, value) {
    chart.data.labels.push(label);
    chart.data.datasets[0].data.push(value);
    if (chart.data.labels.length > CHART_MAX_POINTS) {
        chart.data.labels.shift();
        chart.data.datasets[0].data.shift();
    }
    chart.update('none');
}

// ─── Load Historical Data ──────────────────────────────────────────────────
async function loadHistory() {
    try {
        const res = await apiFetch('/metrics/history?limit=60');
        if (!res.ok) return;
        const { rows } = await res.json();
        rows.forEach(row => {
            const label = `#${row.step}`;
            pushToChart(rewardChart, label, row.reward ?? 0);
            const threatValue = row.threat_level === 'HIGH' ? 0.9 : (row.threat_level === 'MEDIUM' ? 0.5 : 0.1);
            pushToChart(threatChart, label, threatValue);
        });
        logEvent(`Loaded ${rows.length} historical steps`, 'info');
    } catch (e) {
        logEvent('Could not load history — starting fresh', 'warn');
    }
}

// ─── SSE Stream ────────────────────────────────────────────────────────────
function initSSE() {
    const es = new EventSource('/stream');
    const wsChip = document.getElementById('ws-status');

    es.addEventListener('decision', (e) => {
        const d = JSON.parse(e.data);

        // Only update Active Decision panel if the event belongs to the selected drone
        if (d.drone_id === selectedDrone) {
            updateActiveDecisionPanel(d);
        }

        // Header (Step count is global)
        document.getElementById('step-count').textContent = d.step;

        // Active path
        const pathEl = document.getElementById('active-path');
        if (pathEl.textContent !== d.path_name) {
            pathEl.classList.remove('changed');
            void pathEl.offsetWidth; // reflow
            pathEl.classList.add('changed');
        }
        pathEl.textContent = d.path_name || '—';

        // Drone 1 card path
        document.getElementById('drone-1-path').textContent = d.path_name || '—';

        // Topology lanes
        ['direct', 'satellite', 'mesh'].forEach(name => {
            document.getElementById(`lane-${name}`)?.classList.toggle('active', name === d.path_name);
        });

        // Threat level
        const tl = d.threat_level || 'UNKNOWN';
        const threatBlock = document.getElementById('threat-block');
        threatBlock.className = `threat-block threat-${tl}`;
        document.getElementById('threat-label').textContent = tl;

        // Attack badge
        const ab = document.getElementById('attack-badge');
        ab.textContent = `Attack: ${d.attack_type || 'none'}`;
        ab.className = `badge ${d.attack_type && d.attack_type !== 'none' ? 'badge-high' : 'badge-neutral'}`;

        // KPIs
        document.getElementById('kpi-reward').textContent = fmt(d.reward, 3);
        document.getElementById('kpi-confidence').textContent = d.fl_confidence != null ? fmt(d.fl_confidence * 100, 1) + '%' : '—';
        document.getElementById('kpi-recovery').textContent = d.recovery_ms != null ? `${d.recovery_ms} ms` : '—';
        document.getElementById('kpi-loss').textContent = d.packet_loss != null ? fmt(d.packet_loss * 100, 2) + '%' : '—';

        // Charts
        const label = `#${d.step}`;
        pushToChart(rewardChart, label, d.reward ?? 0);
        const threatScore = tl === 'HIGH' ? 0.9 : (tl === 'MEDIUM' ? 0.5 : 0.1);
        pushToChart(threatChart, label, threatScore);

        // Status chip
        wsChip.innerHTML = '<span class="status-dot status-dot-live"></span><span>Live</span>';
    });

    es.onerror = () => {
        wsChip.innerHTML = '<span class="status-dot status-dot-error"></span><span>Reconnecting</span>';
        logEvent('Stream lost, reconnecting…', 'warn');
    };
}

function updateActiveDecisionPanel(d) {
    // Active path
    const pathEl = document.getElementById('active-path');
    if (pathEl.textContent !== d.path_name) {
        pathEl.classList.remove('changed');
        void pathEl.offsetWidth; // reflow
        pathEl.classList.add('changed');
    }
    pathEl.textContent = d.path_name || '—';

    // Topology lanes
    ['direct', 'satellite', 'mesh'].forEach(name => {
        document.getElementById(`lane-${name}`)?.classList.toggle('active', name === d.path_name);
    });

    // Threat level
    const tl = d.threat_level || 'UNKNOWN';
    const threatBlock = document.getElementById('threat-block');
    threatBlock.className = `threat-block threat-${tl}`;
    document.getElementById('threat-label').textContent = tl;

    // Attack badge
    const ab = document.getElementById('attack-badge');
    if (d.attack_type) {
        ab.textContent = `Attack: ${d.attack_type}`;
        ab.className = `badge ${d.attack_type !== 'none' ? 'badge-high' : 'badge-neutral'}`;
    }

    // KPIs
    if (d.reward !== undefined) document.getElementById('kpi-reward').textContent = fmt(d.reward, 3);
    if (d.fl_confidence !== undefined) document.getElementById('kpi-confidence').textContent = d.fl_confidence != null ? fmt(d.fl_confidence * 100, 1) + '%' : '—';
    if (d.recovery_ms !== undefined) document.getElementById('kpi-recovery').textContent = d.recovery_ms != null ? `${d.recovery_ms} ms` : '—';
    if (d.packet_loss !== undefined) document.getElementById('kpi-loss').textContent = d.packet_loss != null ? fmt(d.packet_loss * 100, 2) + '%' : '—';
}

// ─── Live RF Telemetry ─────────────────────────────────────────────────────
const PATH_NAMES = ['direct', 'satellite', 'mesh'];

async function pollTelemetry() {
    try {
        const pollId = selectedDrone === 'all' ? 'drone_1' : selectedDrone;
        const res = await apiFetch(`/metrics/live?drone_id=${pollId}`);
        if (!res.ok) return;
        const data = await res.json();
        if (!data.paths) return;

        const tbody = document.getElementById('telemetry-body');
        tbody.innerHTML = data.paths.map((p, i) => {
            const name = PATH_NAMES[i];
            const isJammed = p.pdr < 0.4;
            const latCls = p.latency > 400 ? 'val-bad' : p.latency > 100 ? 'val-warn' : 'val-ok';
            const pdrCls = p.pdr < 0.5 ? 'val-bad' : p.pdr < 0.85 ? 'val-warn' : 'val-ok';
            const pilCls = isJammed ? 'pill-jammed' : 'pill-ok';
            const pilLabel = isJammed ? '⚡ JAMMED' : '✓ Clear';
            return `
                <tr>
                  <td style="font-weight:600;text-transform:capitalize">${name}</td>
                  <td><span class="status-pill ${pilCls}">${pilLabel}</span></td>
                  <td class="${latCls}">${fmt(p.latency, 1)} ms</td>
                  <td class="${pdrCls}">${fmt(p.pdr * 100, 1)}%</td>
                  <td>${fmt(p.rssi, 1)} dBm</td>
                  <td class="${p.packet_loss > 0.3 ? 'val-bad' : 'val-ok'}">${fmt(p.packet_loss * 100, 1)}%</td>
                </tr>`;
        }).join('');
    } catch (e) {
        console.warn('Telemetry poll failed', e);
    }
}
setInterval(pollTelemetry, 1000);

// ─── Jamming Controls ──────────────────────────────────────────────────────
async function triggerJam(...paths) {
    const label = paths.join(' + ');
    logEvent(`Injecting RF interference on: <strong>${label}</strong> for <strong>${selectedDrone}</strong>`, 'warn');
    try {
        const res = await apiFetch('/jam', {
            method: 'POST',
            body: JSON.stringify({ paths, duration: 10.0, drone_id: selectedDrone }),
        });
        if (res.ok) {
            logEvent(`Jamming ACTIVE on ${label} (${selectedDrone}) — auto-clear in 10s`, 'danger');
        } else {
            const err = await res.json();
            logEvent(`Jam failed: ${err.detail}`, 'warn');
        }
    } catch (e) {
        logEvent(`Error: ${e.message}`, 'warn');
    }
}

async function clearJam() {
    logEvent(`Clearing all RF interference for <strong>${selectedDrone}</strong>…`, 'info');
    try {
        const res = await apiFetch('/jam', {
            method: 'POST',
            body: JSON.stringify({ paths: [], duration: 0, drone_id: selectedDrone }),
        });
        if (res.ok) logEvent(`All jamming threats CLEARED for ${selectedDrone}`, 'success');
    } catch (e) {
        logEvent(`Clear error: ${e.message}`, 'warn');
    }
}

async function pollSwarm() {
    try {
        const res = await apiFetch('/swarm/status');
        if (!res.ok) return;
        const { drones } = await res.json();

        ['drone_1', 'drone_2', 'drone_3'].forEach((id, idx) => {
            const n = idx + 1;
            const d = drones[id];
            if (!d) return;
            const pathEl = document.getElementById(`drone-${n}-path`);
            if (pathEl) pathEl.textContent = d.path_name || '—';

            // Update status pill for drones 2 & 3
            const card = document.getElementById(`drone-card-${n}`);
            if (!card) return;
            const statusEl = card.querySelector('.drone-status');

            if (d.threat_level === 'HIGH') {
                card.style.borderColor = 'rgba(239,68,68,0.35)';
                if (statusEl && n > 1) { statusEl.textContent = 'THREAT'; statusEl.className = 'drone-status'; statusEl.style.color = 'var(--status-high)'; }
            } else if (d.threat_level === 'MEDIUM') {
                card.style.borderColor = 'rgba(245,158,11,0.35)';
                if (statusEl && n > 1) { statusEl.textContent = 'MONITOR'; statusEl.className = 'drone-status'; statusEl.style.color = 'var(--status-medium)'; }
            } else {
                card.style.borderColor = '';
                if (statusEl && n > 1) { statusEl.textContent = 'STANDBY'; statusEl.className = 'drone-status'; statusEl.style.color = 'var(--text-dim)'; }
            }

            // If this is the selected drone, update the active decision panel
            if (selectedDrone === id) {
                updateActiveDecisionPanel(d);
            }

            // Unmute dim cards once we have real data
            if (n > 1) card.classList.remove('drone-card-dim');
        });
    } catch (e) {
        console.warn('Swarm poll failed', e);
    }
}
setInterval(pollSwarm, 2000);

// ─── Drone Selection ───────────────────────────────────────────────────────
function selectDrone(droneId, num) {
    selectedDrone = droneId;
    
    // Update active card styling
    document.querySelectorAll('.drone-card').forEach(el => el.classList.remove('selected-card'));
    const cardId = num === 'All' ? 'drone-card-all' : `drone-card-${num}`;
    document.getElementById(cardId).classList.add('selected-card');
    
    // Update panel titles
    document.getElementById('selected-drone-title').textContent = num === 'All' ? 'All Swarm' : `Drone ${num}`;
    document.getElementById('telemetry-drone-title').textContent = num === 'All' ? 'Drone 1 (Reference)' : `Drone ${num}`;
    const ctrlBadge = document.getElementById('controls-drone-badge');
    if (ctrlBadge) ctrlBadge.textContent = num === 'All' ? 'Target: Entire Swarm' : `Target: Drone ${num}`;
    const ctrlDesc = document.getElementById('controls-drone-desc');
    if (ctrlDesc) ctrlDesc.textContent = num === 'All' ? "the entire swarm's" : `Drone ${num}'s`;
    
    // Immediate fetch to update view
    pollTelemetry();
    pollSwarm();
}

// ─── Initialise ────────────────────────────────────────────────────────────
loadHistory();
initSSE();
pollTelemetry();
pollSwarm();
