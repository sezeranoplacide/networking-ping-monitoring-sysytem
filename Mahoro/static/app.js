// Professional Ping Monitor Pro - Advanced Network Management System
const API_BASE = '/api';

// State Management
let state = {
    devices: [],
    groups: [],
    alerts: [],
    charts: {},
    currentTab: 'dashboard',
    updateInterval: null
};

// DOM Elements
const navButtons = document.querySelectorAll('.nav-btn');
const tabContents = document.querySelectorAll('.tab-content');
const deviceForm = document.getElementById('deviceForm');
const devicesList = document.getElementById('devicesList');
const formMessage = document.getElementById('formMessage');
const modal = document.getElementById('deviceModal');
const groupModal = document.getElementById('groupModal');
const closeButtons = document.querySelectorAll('.close');

// Initialize Application
document.addEventListener('DOMContentLoaded', () => {
    setupNavigation();
    setupEventListeners();
    loadAllData();
    state.updateInterval = setInterval(loadAllData, 5000); // Refresh every 5 seconds
});

function setupNavigation() {
    navButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabName = btn.dataset.tab;
            switchTab(tabName);
        });
    });
}

function setupEventListeners() {
    deviceForm.addEventListener('submit', handleAddDevice);
    closeButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            modal.style.display = 'none';
            groupModal.style.display = 'none';
        });
    });
    window.addEventListener('click', (e) => {
        if (e.target === modal) modal.style.display = 'none';
        if (e.target === groupModal) groupModal.style.display = 'none';
    });

    document.getElementById('createGroupBtn').addEventListener('click', () => {
        groupModal.style.display = 'block';
    });
    document.getElementById('groupForm').addEventListener('submit', handleCreateGroup);
    document.getElementById('filterAllAlerts').addEventListener('click', () => loadAlerts());
    document.getElementById('filterUnacknowledged').addEventListener('click', () => loadAlerts(true));
    document.getElementById('timelineDeviceSelect').addEventListener('change', (e) => {
        if (e.target.value) loadTimeline(parseInt(e.target.value));
    });

    // Monitoring controls
    document.getElementById('startMonitoringBtn').addEventListener('click', startMonitoring);
    document.getElementById('stopMonitoringBtn').addEventListener('click', stopMonitoring);
    
    // Check monitoring status on load
    checkMonitoringStatus();
}

function switchTab(tabName) {
    state.currentTab = tabName;
    
    // Update button states
    navButtons.forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    // Update content visibility
    tabContents.forEach(content => {
        content.classList.toggle('active', content.id === `${tabName}-tab`);
    });

    // Load tab-specific data
    if (tabName === 'analytics') {
        loadStatistics();
        populateTimelineSelect();
    } else if (tabName === 'alerts') {
        loadAlerts();
    }
}

// ==================== DATA LOADING ====================
async function loadAllData() {
    Promise.all([
        loadDevices(),
        loadGroups(),
        loadNetworkSummary(),
        loadAlerts()
    ]);
}

async function loadDevices() {
    try {
        const response = await fetch(`${API_BASE}/devices`);
        if (!response.ok) throw new Error('Failed to fetch devices');
        state.devices = await response.json();
        displayDevices();
        updateCharts();
        updateGroupedDevices();
        populateTimelineSelect();
    } catch (error) {
        console.error('Error loading devices:', error);
    }
}

async function loadGroups() {
    try {
        const response = await fetch(`${API_BASE}/groups`);
        if (!response.ok) throw new Error('Failed to fetch groups');
        state.groups = await response.json();
        displayGroups();
        populateGroupSelect();
    } catch (error) {
        console.error('Error loading groups:', error);
    }
}

async function loadNetworkSummary() {
    try {
        const response = await fetch(`${API_BASE}/network/summary`);
        if (!response.ok) throw new Error('Failed to fetch summary');
        const summary = await response.json();
        updateNetworkSummary(summary);
    } catch (error) {
        console.error('Error loading network summary:', error);
    }
}

async function loadAlerts(unacknowledgedOnly = false) {
    try {
        const url = `${API_BASE}/alerts${unacknowledgedOnly ? '?unacknowledged=true' : ''}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error('Failed to fetch alerts');
        state.alerts = await response.json();
        displayAlerts();
        updateAlertCount();
    } catch (error) {
        console.error('Error loading alerts:', error);
    }
}

async function loadStatistics() {
    try {
        const statsHtml = await Promise.all(
            state.devices.map(async device => {
                const response = await fetch(`${API_BASE}/devices/${device.id}/statistics`);
                return response.json();
            })
        );
        displayStatistics(statsHtml);
    } catch (error) {
        console.error('Error loading statistics:', error);
    }
}

async function loadTimeline(deviceId) {
    try {
        const response = await fetch(`${API_BASE}/devices/${deviceId}/timeline?limit=30`);
        if (!response.ok) throw new Error('Failed to fetch timeline');
        const timeline = await response.json();
        displayTimeline(timeline);
    } catch (error) {
        console.error('Error loading timeline:', error);
    }
}

// ==================== DISPLAY FUNCTIONS ====================
function updateNetworkSummary(summary) {
    document.getElementById('totalDevices').textContent = summary.total_devices;
    document.getElementById('onlineCount').textContent = summary.online_devices;
    document.getElementById('offlineCount').textContent = summary.offline_devices;
    document.getElementById('networkHealth').textContent = summary.network_health_percentage + '%';
    document.getElementById('avgLatency').textContent = summary.average_latency_ms.toFixed(2) + 'ms';
    
    const statusDot = document.querySelector('.status-dot');
    statusDot.className = 'status-dot ' + (summary.network_health_percentage >= 75 ? 'online' : summary.network_health_percentage >= 50 ? 'offline' : 'offline');
}

function updateAlertCount() {
    const unacknowledged = state.alerts.filter(a => !a.is_acknowledged).length;
    document.getElementById('alertCount').textContent = unacknowledged;
}

function displayDevices() {
    if (state.devices.length === 0) {
        devicesList.innerHTML = '<p class="loading">No devices added yet</p>';
        return;
    }

    devicesList.innerHTML = state.devices.map(device => `
        <div class="device-card ${device.status}">
            <div class="device-header">
                <span class="device-name">${escapeHtml(device.name)}</span>
                <span class="status-badge ${device.status}">${device.status.toUpperCase()}</span>
            </div>
            <div class="device-info">
                <strong>IP:</strong> ${device.ip_address}<br>
                <strong>Interval:</strong> ${device.interval}s<br>
                <strong>Uptime:</strong> ${(device.uptime_percentage || 100).toFixed(1)}%
            </div>
            ${device.last_latency_ms ? `
                <div class="device-latency">⚡ ${device.last_latency_ms.toFixed(2)}ms</div>
            ` : ''}
            <div class="device-actions">
                <button class="btn btn-secondary" onclick="editDevice(${device.id})">Edit</button>
                <button class="btn btn-secondary" onclick="viewDetails(${device.id})">Details</button>
                <button class="btn btn-danger" onclick="deleteDeviceConfirm(${device.id})">Delete</button>
            </div>
        </div>
    `).join('');
}

function displayGroups() {
    const groupsList = document.getElementById('groupsList');
    if (state.groups.length === 0) {
        groupsList.innerHTML = '<p>No groups created yet</p>';
        return;
    }

    groupsList.innerHTML = state.groups.map(group => {
        const deviceCount = state.devices.filter(d => d.group_name === group.name).length;
        return `
            <div class="group-card" style="border-left-color: ${group.color}">
                <div class="group-name">${escapeHtml(group.name)}</div>
                <div class="group-description">${escapeHtml(group.description || '')}</div>
                <div class="group-count">${deviceCount} devices</div>
            </div>
        `;
    }).join('');
}

function updateGroupedDevices() {
    const container = document.getElementById('groupedDevices');
    
    if (state.devices.length === 0) {
        container.innerHTML = '<p class="loading">No devices to display</p>';
        return;
    }

    const grouped = {};
    state.devices.forEach(device => {
        const group = device.group_name || 'Ungrouped';
        if (!grouped[group]) grouped[group] = [];
        grouped[group].push(device);
    });

    container.innerHTML = Object.entries(grouped).map(([groupName, devices]) => {
        const onlineCount = devices.filter(d => d.status === 'online').length;
        const healthPercent = Math.round(onlineCount / devices.length * 100);

        return `
            <div class="group-section">
                <h4>${escapeHtml(groupName)} <span style="color: #999; font-size: 0.9em;">(${onlineCount}/${devices.length} online - ${healthPercent}%)</span></h4>
                <div class="group-device-list">
                    ${devices.map(d => `
                        <div class="group-device-item" style="border-left-color: ${d.status === 'online' ? '#2ecc71' : '#e74c3c'}">
                            <div class="group-device-name">${escapeHtml(d.name)}</div>
                            <div class="group-device-status">
                                <span class="status-badge ${d.status}">${d.status}</span>
                                ${d.last_latency_ms ? `<span style="margin-left: 5px;">⚡ ${d.last_latency_ms.toFixed(1)}ms</span>` : ''}
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }).join('');
}

function displayStatistics(stats) {
    const container = document.getElementById('statisticsList');
    
    container.innerHTML = stats.map(stat => `
        <div class="stat-item">
            <div class="stat-row">
                <span class="stat-label">Device</span>
                <span class="stat-value">${escapeHtml(stat.device_name)}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Total Pings</span>
                <span class="stat-value">${stat.total_pings}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Uptime</span>
                <span class="stat-value">${stat.uptime_percentage}%</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Avg Latency</span>
                <span class="stat-value">${stat.avg_latency_ms.toFixed(2)}ms</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Min Latency</span>
                <span class="stat-value">${stat.min_latency_ms.toFixed(2)}ms</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Max Latency</span>
                <span class="stat-value">${stat.max_latency_ms.toFixed(2)}ms</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Status Changes</span>
                <span class="stat-value">${stat.status_changes}</span>
            </div>
        </div>
    `).join('');
}

function displayTimeline(timeline) {
    const container = document.getElementById('timelineContainer');
    
    if (timeline.length === 0) {
        container.innerHTML = '<p class="loading">No status changes recorded</p>';
        return;
    }

    container.innerHTML = `
        <div class="timeline-container">
            ${timeline.map(item => {
                const marker = item.to_status === 'online' ? '📈' : '📉';
                const markerClass = item.to_status === 'online' ? 'up' : 'down';
                const durationText = item.duration_seconds 
                    ? `Duration: ${formatDuration(item.duration_seconds)}`
                    : '';

                return `
                    <div class="timeline-item">
                        <div class="timeline-marker ${markerClass}">${marker}</div>
                        <div class="timeline-content">
                            <div class="timeline-time">${formatDate(item.recorded_at)}</div>
                            <div class="timeline-text">
                                Transitioned from <strong>${item.from_status.toUpperCase()}</strong> to <strong>${item.to_status.toUpperCase()}</strong>
                            </div>
                            ${durationText ? `<div class="timeline-duration">${durationText}</div>` : ''}
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function displayAlerts() {
    const container = document.getElementById('alertsList');
    
    if (state.alerts.length === 0) {
        container.innerHTML = '<p class="loading">No alerts</p>';
        return;
    }

    container.innerHTML = state.alerts.map(alert => `
        <div class="alert-item ${alert.severity}">
            <div class="alert-content">
                <div class="alert-type">${escapeHtml(alert.alert_type)}</div>
                <div class="alert-message">${escapeHtml(alert.message)}</div>
                <div class="alert-time">${formatDate(alert.created_at)}</div>
            </div>
            ${!alert.is_acknowledged ? `
                <button class="btn btn-secondary" onclick="acknowledgeAlert(${alert.id})">Acknowledge</button>
            ` : `<span style="color: #999;">Acknowledged</span>`}
        </div>
    `).join('');
}

// ==================== DEVICE MANAGEMENT ====================
async function handleAddDevice(e) {
    e.preventDefault();

    const newDevice = {
        name: document.getElementById('deviceName').value.trim(),
        ip_address: document.getElementById('deviceIP').value.trim(),
        interval: parseInt(document.getElementById('deviceInterval').value),
        timeout: parseInt(document.getElementById('deviceTimeout').value)
    };

    if (!newDevice.name || !newDevice.ip_address) {
        showMessage('Please fill in all required fields', 'error');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/devices`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newDevice)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to add device');
        }

        const groupName = document.getElementById('deviceGroup').value;
        if (groupName) {
            const device = await response.json();
            await fetch(`${API_BASE}/devices/${device.id}/assign-group`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ group_name: groupName })
            });
        }

        showMessage('Device added successfully!', 'success');
        deviceForm.reset();
        loadAllData();
    } catch (error) {
        showMessage(`Error: ${error.message}`, 'error');
    }
}

async function editDevice(deviceId) {
    const device = state.devices.find(d => d.id === deviceId);
    if (!device) return;

    const newInterval = prompt('Enter interval (seconds):', device.interval);
    if (newInterval === null) return;

    const newTimeout = prompt('Enter timeout (seconds):', device.timeout);
    if (newTimeout === null) return;

    try {
        const response = await fetch(`${API_BASE}/devices/${deviceId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                interval: parseInt(newInterval),
                timeout: parseInt(newTimeout)
            })
        });

        if (!response.ok) throw new Error('Failed to update device');
        loadAllData();
        showMessage('Device updated successfully!', 'success');
    } catch (error) {
        showMessage(`Error: ${error.message}`, 'error');
    }
}

async function viewDetails(deviceId) {
    const device = state.devices.find(d => d.id === deviceId);
    if (!device) return;

    const stats = await fetch(`${API_BASE}/devices/${deviceId}/statistics`).then(r => r.json());
    const lastSeen = device.last_seen_at ? new Date(device.last_seen_at).toLocaleString() : 'Never';

    const modalBody = document.getElementById('modalBody');
    modalBody.innerHTML = `
        <h2 style="margin-bottom: 20px;">${escapeHtml(device.name)}</h2>
        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px;">
            <div>
                <p><strong>IP Address:</strong> ${device.ip_address}</p>
                <p><strong>Status:</strong> <span class="status-badge ${device.status}">${device.status.toUpperCase()}</span></p>
                <p><strong>Last Latency:</strong> ${device.last_latency_ms ? device.last_latency_ms.toFixed(2) + 'ms' : 'N/A'}</p>
                <p><strong>Last Seen:</strong> ${lastSeen}</p>
            </div>
            <div>
                <p><strong>Interval:</strong> ${device.interval}s</p>
                <p><strong>Timeout:</strong> ${device.timeout}s</p>
                <p><strong>Uptime:</strong> ${stats.uptime_percentage}%</p>
                <p><strong>Total Pings:</strong> ${stats.total_pings}</p>
            </div>
        </div>
        <div style="margin-top: 20px;">
            <p><strong>Performance Metrics (24h)</strong></p>
            <p>Avg Latency: ${stats.avg_latency_ms.toFixed(2)}ms | Min: ${stats.min_latency_ms.toFixed(2)}ms | Max: ${stats.max_latency_ms.toFixed(2)}ms</p>
            <p>Status Changes: ${stats.status_changes}</p>
        </div>
    `;

    modal.style.display = 'block';
}

function deleteDeviceConfirm(deviceId) {
    const device = state.devices.find(d => d.id === deviceId);
    if (!device) return;

    if (confirm(`Are you sure you want to delete "${device.name}"?`)) {
        deleteDevice(deviceId);
    }
}

async function deleteDevice(deviceId) {
    try {
        const response = await fetch(`${API_BASE}/devices/${deviceId}`, {
            method: 'DELETE'
        });

        if (!response.ok) throw new Error('Failed to delete device');
        loadAllData();
        showMessage('Device deleted successfully!', 'success');
    } catch (error) {
        showMessage(`Error: ${error.message}`, 'error');
    }
}

// ==================== GROUP MANAGEMENT ====================
async function handleCreateGroup(e) {
    e.preventDefault();

    const group = {
        name: document.getElementById('groupName').value.trim(),
        description: document.getElementById('groupDescription').value.trim(),
        color: document.getElementById('groupColor').value
    };

    if (!group.name) {
        alert('Group name is required');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/groups`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(group)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to create group');
        }

        showMessage('Group created successfully!', 'success');
        document.getElementById('groupForm').reset();
        groupModal.style.display = 'none';
        loadAllData();
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

function populateGroupSelect() {
    const select = document.getElementById('deviceGroup');
    select.innerHTML = '<option value="">-- No Group --</option>' +
        state.groups.map(g => `<option value="${g.name}">${escapeHtml(g.name)}</option>`).join('');
}

function populateTimelineSelect() {
    const select = document.getElementById('timelineDeviceSelect');
    select.innerHTML = '<option value="">-- Select Device --</option>' +
        state.devices.map(d => `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join('');
}

// ==================== ALERTS ====================
async function acknowledgeAlert(alertId) {
    try {
        const response = await fetch(`${API_BASE}/alerts/${alertId}/acknowledge`, {
            method: 'POST'
        });

        if (!response.ok) throw new Error('Failed to acknowledge alert');
        loadAlerts();
        showMessage('Alert acknowledged', 'success');
    } catch (error) {
        showMessage(`Error: ${error.message}`, 'error');
    }
}

// ==================== CHARTS ====================
function updateCharts() {
    updateStatusChart();
    updateLatencyChart();
}

function updateStatusChart() {
    const statusCanvas = document.getElementById('statusChart');
    
    const statusCounts = {
        online: state.devices.filter(d => d.status === 'online').length,
        offline: state.devices.filter(d => d.status === 'offline').length,
        unknown: state.devices.filter(d => d.status === 'unknown').length
    };

    if (state.charts.status) state.charts.status.destroy();

    state.charts.status = new Chart(statusCanvas, {
        type: 'doughnut',
        data: {
            labels: ['Online', 'Offline', 'Unknown'],
            datasets: [{
                data: [statusCounts.online, statusCounts.offline, statusCounts.unknown],
                backgroundColor: ['#2ecc71', '#e74c3c', '#95a5a6'],
                borderColor: ['#27ae60', '#c0392b', '#7f8c8d'],
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'bottom' },
                title: { display: false }
            }
        }
    });
}

function updateLatencyChart() {
    const latencyCanvas = document.getElementById('latencyChart');
    
    const deviceNames = state.devices.map(d => d.name.substring(0, 20));
    const latencies = state.devices.map(d => d.last_latency_ms || 0);

    if (state.charts.latency) state.charts.latency.destroy();

    state.charts.latency = new Chart(latencyCanvas, {
        type: 'bar',
        data: {
            labels: deviceNames,
            datasets: [{
                label: 'Latency (ms)',
                data: latencies,
                backgroundColor: '#667eea',
                borderColor: '#764ba2',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                legend: { display: true },
                title: { display: false }
            },
            scales: {
                x: { beginAtZero: true }
            }
        }
    });
}

// ==================== UTILITY FUNCTIONS ====================
function showMessage(text, type) {
    formMessage.textContent = text;
    formMessage.className = `message ${type}`;
    setTimeout(() => {
        formMessage.className = 'message';
    }, 3000);
}

function escapeHtml(text) {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return text.replace(/[&<>"']/g, m => map[m]);
}

function formatDate(isoString) {
    if (!isoString) return '';
    const date = new Date(isoString);
    return date.toLocaleString();
}

function formatDuration(seconds) {
    if (!seconds) return '';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
}

// ==================== MONITORING CONTROL ====================
async function startMonitoring() {
    try {
        const response = await fetch(`${API_BASE}/monitoring/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ interval: 5 })
        });
        
        if (!response.ok) throw new Error('Failed to start monitoring');
        
        const result = await response.json();
        showMessage('Automatic ping monitoring started! ✓', 'success');
        updateMonitoringUI(true);
        
        // Start refresh cycle for real-time updates
        if (!state.refreshInterval) {
            state.refreshInterval = setInterval(loadAllData, 5000);
        }
    } catch (error) {
        showMessage(`Error: ${error.message}`, 'error');
    }
}

async function stopMonitoring() {
    try {
        const response = await fetch(`${API_BASE}/monitoring/stop`, {
            method: 'POST'
        });
        
        if (!response.ok) throw new Error('Failed to stop monitoring');
        
        showMessage('Automatic ping monitoring stopped', 'success');
        updateMonitoringUI(false);
        
        // Stop refresh cycle
        if (state.refreshInterval) {
            clearInterval(state.refreshInterval);
            state.refreshInterval = null;
        }
    } catch (error) {
        showMessage(`Error: ${error.message}`, 'error');
    }
}

async function checkMonitoringStatus() {
    try {
        const response = await fetch(`${API_BASE}/monitoring/status`);
        if (!response.ok) throw new Error('Failed to check status');
        
        const status = await response.json();
        updateMonitoringUI(status.is_running);
    } catch (error) {
        console.error('Error checking monitoring status:', error);
    }
}

function updateMonitoringUI(isRunning) {
    const startBtn = document.getElementById('startMonitoringBtn');
    const stopBtn = document.getElementById('stopMonitoringBtn');
    const statusIndicator = document.getElementById('monitoringStatusIndicator');
    
    if (isRunning) {
        startBtn.style.display = 'none';
        stopBtn.style.display = 'inline-block';
        statusIndicator.className = 'status-indicator running';
        statusIndicator.textContent = '🔴 MONITORING ACTIVE';
    } else {
        startBtn.style.display = 'inline-block';
        stopBtn.style.display = 'none';
        statusIndicator.className = 'status-indicator stopped';
        statusIndicator.textContent = 'STOPPED';
    }
}
