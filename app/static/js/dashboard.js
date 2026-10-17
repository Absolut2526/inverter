// Real-time Dashboard Logic with WebSockets and Chart.js

let powerChart = null;
let ws = null;
let reconnectTimer = null;

// Initialize Chart.js
function initChart() {
  const ctx = document.getElementById('powerChart').getContext('2d');
  
  const gradientSolar = ctx.createLinearGradient(0, 0, 0, 300);
  gradientSolar.addColorStop(0, 'rgba(245, 158, 11, 0.4)');
  gradientSolar.addColorStop(1, 'rgba(245, 158, 11, 0.0)');

  const gradientLoad = ctx.createLinearGradient(0, 0, 0, 300);
  gradientLoad.addColorStop(0, 'rgba(236, 72, 153, 0.3)');
  gradientLoad.addColorStop(1, 'rgba(236, 72, 153, 0.0)');

  powerChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'Сонце (Вт)',
          borderColor: '#f59e0b',
          backgroundColor: gradientSolar,
          borderWidth: 2,
          fill: true,
          tension: 0.3,
          data: [],
          pointRadius: 0,
        },
        {
          label: 'Будинок (Вт)',
          borderColor: '#ec4899',
          backgroundColor: gradientLoad,
          borderWidth: 2,
          fill: true,
          tension: 0.3,
          data: [],
          pointRadius: 0,
        },
        {
          label: 'Батарея (Вт)',
          borderColor: '#10b981',
          borderWidth: 2,
          fill: false,
          tension: 0.3,
          data: [],
          pointRadius: 0,
        },
        {
          label: 'Мережа (Вт)',
          borderColor: '#3b82f6',
          borderWidth: 1.5,
          borderDash: [4, 4],
          fill: false,
          tension: 0.3,
          data: [],
          pointRadius: 0,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false,
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1e293b',
          titleColor: '#f1f5f9',
          bodyColor: '#cbd5e1',
          borderColor: '#475569',
          borderWidth: 1,
          padding: 10,
        }
      },
      scales: {
        x: {
          grid: { color: 'rgba(255, 255, 255, 0.05)' },
          ticks: { color: '#64748b', maxTicksLimit: 8 }
        },
        y: {
          grid: { color: 'rgba(255, 255, 255, 0.05)' },
          ticks: {
            color: '#64748b',
            callback: (v) => v + ' W'
          }
        }
      }
    }
  });
}

// Fetch initial historical data
async function loadHistory() {
  try {
    const res = await fetch('/api/history');
    if (!res.ok) return;
    const history = await res.json();
    if (powerChart && history.length > 0) {
      powerChart.data.labels = history.map(p => p.time);
      powerChart.data.datasets[0].data = history.map(p => p.solar_power);
      powerChart.data.datasets[1].data = history.map(p => p.load_power);
      powerChart.data.datasets[2].data = history.map(p => p.battery_power);
      powerChart.data.datasets[3].data = history.map(p => p.grid_power);
      powerChart.update('none');
    }
  } catch (e) {
    console.error("Failed to load history:", e);
  }
}

// Connect to WebSocket for live streaming
function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  const wsIndicator = document.getElementById('ws-indicator');
  const wsStatus = document.getElementById('ws-status');

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log("WebSocket connected to Deye Monitor");
    if (wsIndicator) {
      wsIndicator.className = "w-2 h-2 rounded-full bg-emerald-500 animate-pulse";
    }
    if (wsStatus) {
      wsStatus.innerText = "В реальному часі (Online)";
    }
    if (reconnectTimer) clearTimeout(reconnectTimer);
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      updateUI(data);
    } catch (e) {
      console.error("Error processing telemetry packet:", e);
    }
  };

  ws.onclose = () => {
    console.warn("WebSocket disconnected. Reconnecting in 3s...");
    if (wsIndicator) {
      wsIndicator.className = "w-2 h-2 rounded-full bg-amber-500";
    }
    if (wsStatus) {
      wsStatus.innerText = "Перепідключення...";
    }
    reconnectTimer = setTimeout(connectWebSocket, 3000);
  };

  ws.onerror = (err) => {
    console.error("WebSocket error:", err);
    ws.close();
  };
}

// Update DOM elements with new telemetry data
function updateUI(t) {
  if (!t) return;

  // Last update time
  const timeStr = new Date(t.timestamp).toLocaleTimeString();
  document.getElementById('last-updated-time').innerText = timeStr;

  // Solar
  document.getElementById('pv-total-power').innerText = `${Math.round(t.solar.total_power)} Вт`;
  document.getElementById('pv-daily-energy').innerText = `${t.solar.daily_energy.toFixed(1)} кВт⋅г`;
  document.getElementById('pv1-power').innerText = `${Math.round(t.solar.pv1_power)} Вт`;
  document.getElementById('pv1-v-a').innerText = `${t.solar.pv1_voltage}V • ${t.solar.pv1_current}A`;
  document.getElementById('pv2-power').innerText = `${Math.round(t.solar.pv2_power)} Вт`;
  document.getElementById('pv2-v-a').innerText = `${t.solar.pv2_voltage}V • ${t.solar.pv2_current}A`;

  // Battery
  const totalSoc = Math.round(t.battery.total_soc);
  document.getElementById('batt-total-soc').innerText = `${totalSoc}%`;
  
  const battBar = document.getElementById('batt-progress-bar');
  battBar.style.width = `${totalSoc}%`;
  if (totalSoc < 20) {
    battBar.className = "bg-gradient-to-r from-red-600 to-rose-500 h-2.5 rounded-full";
  } else if (totalSoc < 40) {
    battBar.className = "bg-gradient-to-r from-amber-500 to-yellow-400 h-2.5 rounded-full";
  } else {
    battBar.className = "bg-gradient-to-r from-emerald-500 to-teal-400 h-2.5 rounded-full";
  }

  const battPower = Math.round(t.battery.total_power);
  const battBadge = document.getElementById('batt-power-badge');
  if (battPower > 20) {
    battBadge.innerText = `+${battPower} Вт (Заряд)`;
    battBadge.className = "text-xs font-bold px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300";
  } else if (battPower < -20) {
    battBadge.innerText = `${battPower} Вт (Розряд)`;
    battBadge.className = "text-xs font-bold px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300";
  } else {
    battBadge.innerText = "0 Вт (Сплячий)";
    battBadge.className = "text-xs font-bold px-2 py-0.5 rounded-full bg-slate-700 text-slate-300";
  }

  // Estimated remaining time
  const timeRemainEl = document.getElementById('batt-time-remain');
  if (t.battery.estimated_remaining_hours) {
    const action = battPower < -20 ? "розряду" : "заряду";
    timeRemainEl.innerText = `~${t.battery.estimated_remaining_hours} год до ${action}`;
  } else {
    timeRemainEl.innerText = `${t.battery.voltage}В • ${t.battery.current}А`;
  }

  // Dual Battery Cards (Battery 1 and Battery 2)
  if (t.battery.batteries && t.battery.batteries.length >= 2) {
    const b1 = t.battery.batteries[0];
    const b2 = t.battery.batteries[1];
    document.getElementById('b1-soc').innerText = `${b1.soc}%`;
    document.getElementById('b1-details').innerText = `${b1.voltage}V • ${b1.temperature}°C`;
    document.getElementById('b2-soc').innerText = `${b2.soc}%`;
    document.getElementById('b2-details').innerText = `${b2.voltage}V • ${b2.temperature}°C`;
  }

  // Inverter
  document.getElementById('inverter-mode').innerText = `Режим: ${t.inverter.work_mode}`;
  document.getElementById('inverter-temp').innerText = `${t.inverter.temperature}°C`;
  document.getElementById('inverter-status').innerText = t.inverter.fault_code === 0 ? "Норма" : `Код ${t.inverter.fault_code}`;

  // Load
  document.getElementById('load-power').innerText = `${Math.round(t.load.power)} Вт`;
  document.getElementById('load-daily-energy').innerText = `${t.load.daily_energy.toFixed(1)} кВт⋅г`;

  // Grid
  const isOnline = t.grid.is_online;
  const blackoutBanner = document.getElementById('blackout-banner');
  const gridCard = document.getElementById('grid-card');
  const gridBadge = document.getElementById('grid-status-badge');
  const gridPowerEl = document.getElementById('grid-power');

  if (isOnline) {
    blackoutBanner.classList.add('hidden');
    gridCard.className = "bg-gradient-to-br from-blue-500/10 via-slate-800/40 to-slate-900/60 border border-blue-500/30 rounded-2xl p-4";
    gridBadge.innerText = "Є світло";
    gridBadge.className = "text-xs font-bold px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30";
    
    const gp = Math.round(t.grid.power);
    if (gp > 30) {
      gridPowerEl.innerText = `+${gp} Вт (Імпорт)`;
      gridPowerEl.className = "text-xl font-black text-blue-400";
    } else if (gp < -30) {
      gridPowerEl.innerText = `${gp} Вт (Експорт)`;
      gridPowerEl.className = "text-xl font-black text-emerald-400";
    } else {
      gridPowerEl.innerText = "0 Вт (Баланс)";
      gridPowerEl.className = "text-xl font-black text-slate-300";
    }

    document.getElementById('grid-v-hz').innerText = `${t.grid.voltage} В • ${t.grid.frequency} Гц`;
    document.getElementById('grid-daily-balance').innerText = `Імпорт: ${t.grid.daily_import.toFixed(1)} кВт⋅г`;
  } else {
    blackoutBanner.classList.remove('hidden');
    gridCard.className = "bg-gradient-to-br from-red-500/10 via-slate-800/40 to-slate-900/60 border border-red-500/50 rounded-2xl p-4";
    gridBadge.innerText = "Блекаут";
    gridBadge.className = "text-xs font-bold px-2 py-0.5 rounded-full bg-red-500/20 text-red-400 border border-red-500/30 animate-pulse";
    gridPowerEl.innerText = "0 В (Немає)";
    gridPowerEl.className = "text-xl font-black text-red-400";
    document.getElementById('grid-v-hz').innerText = "0.0 В • 0.0 Гц";
    document.getElementById('grid-daily-balance').innerText = "Живлення від АКБ";
  }

  // Update Chart
  if (powerChart) {
    const timeLabel = new Date(t.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    powerChart.data.labels.push(timeLabel);
    powerChart.data.datasets[0].data.push(t.solar.total_power);
    powerChart.data.datasets[1].data.push(t.load.power);
    powerChart.data.datasets[2].data.push(t.battery.total_power);
    powerChart.data.datasets[3].data.push(t.grid.power);

    if (powerChart.data.labels.length > 50) {
      powerChart.data.labels.shift();
      powerChart.data.datasets.forEach(d => d.data.shift());
    }
    powerChart.update('none');
  }
}

// Demo helper to test blackout toggle
async function toggleTestGrid() {
  try {
    const res = await fetch('/api/test/toggle-grid', { method: 'POST' });
    const data = await res.json();
    console.log("Grid state toggled:", data);
  } catch (e) {
    console.error("Failed to toggle test grid:", e);
  }
}

// Setup Telegram bot link if configured
async function setupBotLinks() {
  try {
    const res = await fetch('/api/status');
    // If bot token has username or name
  } catch(e) {}
}

// Start
document.addEventListener('DOMContentLoaded', () => {
  lucide.createIcons();
  initChart();
  loadHistory();
  connectWebSocket();
});
