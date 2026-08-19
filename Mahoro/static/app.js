// Professional Ping Monitor Pro - Advanced Network Management System
const API_BASE = '/api';

// Every state-changing request must carry the CSRF token the server issued for
// this session. Wrapping fetch keeps it out of two dozen individual call sites.
const nativeFetch = window.fetch.bind(window);
window.fetch = function (resource, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && state.csrfToken) {
        options = { ...options, headers: { ...(options.headers || {}), 'X-CSRF-Token': state.csrfToken } };
    }
    return nativeFetch(resource, options);
};

// State Management
let state = {
    devices: [],
    groups: [],
    alerts: [],
    charts: {},
    currentTab: 'dashboard',
    refreshInterval: null,
    currentUser: null,
    csrfToken: null,
    isAdmin: false,
    topology: null,
    pendingToasts: [],
    notificationsPrimed: false,
    editingDeviceId: null
};
state.notifications = [];
state._seenNotificationIds = new Set();

// DOM Elements
const navButtons = document.querySelectorAll('.nav-btn');
const tabContents = document.querySelectorAll('.tab-content');
const deviceForm = document.getElementById('deviceForm');
const devicesList = document.getElementById('devicesList');
const formMessage = document.getElementById('formMessage');
const modal = document.getElementById('deviceModal');
const groupModal = document.getElementById('groupModal');

// Initialize Application
document.addEventListener('DOMContentLoaded', async () => {
    const authenticated = await checkAuthStatus();
    if (!authenticated) {
        window.location.href = '/login';
        return;
    }

    setupNavigation();
    setupEventListeners();
    applyRolePermissions();
    loadAllData();
    state.refreshInterval = setInterval(loadAllData, 5000); // Refresh every 5 seconds
    // Poll notifications separately
    loadNotifications();
    setInterval(loadNotifications, 10000);
});

async function checkAuthStatus() {
    try {
        const response = await fetch(`${API_BASE}/auth/status`);
        if (!response.ok) return false;
        const data = await response.json();
        state.currentUser = data;
        state.csrfToken = data.csrf_token || null;
        state.isAdmin = data.is_admin === true;
        return data.authenticated === true;
    } catch (error) {
        console.error('Error checking auth status:', error);
        return false;
    }
}

function setupNavigation() {
    navButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabName = btn.dataset.tab;
            switchTab(tabName);
        });
    });
}

function setupEventListeners() {
    if (deviceForm) {
        deviceForm.addEventListener('submit', handleDeviceFormSubmit);
    }

    document.getElementById('addDeviceBtn')?.addEventListener('click', () => openDeviceForm());

    // Escape closes whatever dialog is open.
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        document.querySelectorAll('.modal').forEach(m => {
            if (m.style.display === 'block') closeModal(m);
        });
    });

    if (devicesList) {
        devicesList.addEventListener('click', handleDeviceCardClick);
    }

    const typeSelect = document.getElementById('deviceType');
    if (typeSelect) typeSelect.addEventListener('change', syncDeviceFormOptions);

    // Each of these is independent. Wiring them separately means a failure in one
    // cannot leave the navigation, the device list or the dialogs unbound — which
    // is what an uncaught error here used to do to the entire page.
    [setupThemeToggle, setupAccountControls, setupDiagnostics, setupConsole].forEach(init => {
        try {
            init();
        } catch (error) {
            console.error(`${init.name} failed to initialise:`, error);
        }
    });

    const viewport = document.getElementById('topologyViewport');
    if (viewport) {
        viewport.addEventListener('click', handleTopologyClick);
        viewport.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') handleTopologyClick(e);
        });
    }

    // Polling used to continue in hidden tabs. Refresh immediately on return
    // instead, so a backgrounded dashboard costs nothing.
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') loadAllData();
    });

    // Delegated, so every dialog is covered — including ones added later. The old
    // handler closed two modals by name, so the × on any other dialog did nothing.
    document.addEventListener('click', (e) => {
        const closer = e.target.closest('.close, [data-close-modal]');
        if (closer) {
            closeModal(closer.closest('.modal'));
            return;
        }
        // Clicking the backdrop closes the dialog it belongs to.
        if (e.target.classList?.contains('modal')) closeModal(e.target);
    });

    const createGroupBtn = document.getElementById('createGroupBtn');
    if (createGroupBtn) {
        createGroupBtn.addEventListener('click', () => {
            if (groupModal) groupModal.style.display = 'block';
        });
    }

    const groupForm = document.getElementById('groupForm');
    if (groupForm) {
        groupForm.addEventListener('submit', handleCreateGroup);
    }

    const filterAllAlertsBtn = document.getElementById('filterAllAlerts');
    if (filterAllAlertsBtn) {
        filterAllAlertsBtn.addEventListener('click', () => loadAlerts());
    }

    const filterUnacknowledgedBtn = document.getElementById('filterUnacknowledged');
    if (filterUnacknowledgedBtn) {
        filterUnacknowledgedBtn.addEventListener('click', () => loadAlerts(true));
    }

    const timelineSelect = document.getElementById('timelineDeviceSelect');
    if (timelineSelect) {
        timelineSelect.addEventListener('change', (e) => {
            if (e.target.value) loadTimeline(parseInt(e.target.value));
        });
    }

    const deviceSearch = document.getElementById('deviceSearch');
    if (deviceSearch) {
        deviceSearch.addEventListener('input', () => displayDevices());
    }

    const deviceStatusFilter = document.getElementById('deviceStatusFilter');
    if (deviceStatusFilter) {
        deviceStatusFilter.addEventListener('change', () => displayDevices());
    }

    const deviceGroupFilter = document.getElementById('deviceGroupFilter');
    if (deviceGroupFilter) {
        deviceGroupFilter.addEventListener('change', () => displayDevices());
    }

    const resetFiltersBtn = document.getElementById('resetFilters');
    if (resetFiltersBtn) {
        resetFiltersBtn.addEventListener('click', () => {
            if (deviceSearch) deviceSearch.value = '';
            if (deviceStatusFilter) deviceStatusFilter.value = '';
            if (deviceGroupFilter) deviceGroupFilter.value = '';
            displayDevices();
        });
    }

    const startMonitoringBtn = document.getElementById('startMonitoringBtn');
    if (startMonitoringBtn) {
        startMonitoringBtn.addEventListener('click', startMonitoring);
    }

    const stopMonitoringBtn = document.getElementById('stopMonitoringBtn');
    if (stopMonitoringBtn) {
        stopMonitoringBtn.addEventListener('click', stopMonitoring);
    }

    const saveGatewayBtn = document.getElementById('saveGatewayBtn');
    if (saveGatewayBtn) {
        saveGatewayBtn.addEventListener('click', saveNetworkGateway);
    }

    checkMonitoringStatus();

    // Load users panel if present (admin)
    const usersPanel = document.getElementById('usersPanel');
    if (usersPanel) loadUsersPanel();

    const openNotificationsBtn = document.getElementById('openNotificationsBtn');
    if (openNotificationsBtn) {
        openNotificationsBtn.addEventListener('click', () => {
            const modal = document.getElementById('notificationsModal');
            if (modal) modal.style.display = 'block';
            loadNotificationsPanel();
        });
    }
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
    await Promise.all([
        loadDevices(),
        loadGroups(),
        loadNetworkSummary(),
        loadAlerts(),
        loadTopology(),
        loadDiagnostics(),
        loadConsole()
    ]);
}

async function loadTopology() {
    try {
        const response = await fetch(`${API_BASE}/topology`);
        if (!response.ok) throw new Error('Failed to fetch topology');
        state.topology = await response.json();

        // The guard compares the rendered markup, so these can be called freely.
        renderTopology(state.topology);
        renderIncidents(state.topology);

        // The dialog reads its options from state, so only refresh them when it is
        // closed — rebuilding a <select> under an open dropdown closes it.
        if (!isModalOpen()) syncDeviceFormOptions();
    } catch (error) {
        console.error('Error loading topology:', error);
    }
}

/* The form has to reflect what the network can actually accept: an endpoint needs
   an uplink, and if nothing can serve as one yet the form says so instead of
   letting the request fail at the server. */
function syncDeviceFormOptions() {
    const typeSelect = document.getElementById('deviceType');
    const parentSelect = document.getElementById('deviceParent');
    const hint = document.getElementById('deviceParentHint');
    if (!typeSelect || !parentSelect || !state.topology) return;

    const types = state.topology.device_types || [];
    renderOptions('deviceTypes', typeSelect,
        types.map(t => ({ value: t.value, label: t.label })));
    if (!typeSelect.value) typeSelect.value = types[0]?.value || 'other';

    // A device cannot hang off itself, nor off anything already below it.
    const blocked = new Set();
    if (state.editingDeviceId !== null) {
        blocked.add(state.editingDeviceId);
        const nodes = state.topology.nodes || [];
        let frontier = [state.editingDeviceId];
        while (frontier.length) {
            const current = frontier.pop();
            nodes.filter(n => n.parent_id === current).forEach(child => {
                if (!blocked.has(child.id)) { blocked.add(child.id); frontier.push(child.id); }
            });
        }
    }
    const uplinks = (state.topology.nodes || [])
        .filter(n => n.is_infrastructure && !blocked.has(n.id));
    const chosenType = types.find(t => t.value === typeSelect.value);
    const isInfrastructure = chosenType ? chosenType.infrastructure : false;

    const options = uplinks.map(n => ({
        value: String(n.id),
        label: `${n.name} · ${n.type_label} · ${n.ip_address}`
    }));

    if (isInfrastructure) {
        renderOptions('uplinks', parentSelect, options,
            { placeholder: { value: '', label: 'Nothing — this is the top of the network' } });
        parentSelect.required = false;
        hint.textContent = uplinks.length
            ? 'Leave blank if this sits at the top of the network.'
            : 'This will be the first device on the map.';
    } else if (uplinks.length === 0) {
        renderOptions('uplinks', parentSelect, [],
            { placeholder: { value: '', label: 'No switch or router yet' } });
        parentSelect.required = true;
        hint.textContent = 'Add a router or switch first — this device has to plug into something.';
    } else {
        renderOptions('uplinks', parentSelect, options,
            { placeholder: { value: '', label: 'Choose an uplink…' } });
        parentSelect.required = true;
        hint.textContent = 'The switch or router this device plugs into.';
    }
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

function getFilteredDevices() {
    const searchTerm = document.getElementById('deviceSearch')?.value.trim().toLowerCase() || '';
    const statusFilter = document.getElementById('deviceStatusFilter')?.value || '';
    const groupFilter = document.getElementById('deviceGroupFilter')?.value || '';

    return state.devices.filter(device => {
        const matchesSearch = searchTerm === '' || device.name.toLowerCase().includes(searchTerm) || device.ip_address.toLowerCase().includes(searchTerm);
        const matchesStatus = statusFilter === '' || device.status === statusFilter;
        const deviceGroupName = device.group_name || 'Ungrouped';
        const matchesGroup = groupFilter === '' || deviceGroupName === groupFilter;
        return matchesSearch && matchesStatus && matchesGroup;
    });
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

// The navbar used to read "Network OK" unconditionally, whatever the data said.
function updateNetworkStatusIndicator(summary) {
    const dot = document.getElementById('networkStatusDot');
    const label = document.getElementById('networkStatusText');
    if (!dot || !label) return;

    const offline = summary.offline_devices || 0;
    const health = summary.network_health_percentage;

    let cls = 'unknown';
    let text = 'No devices polled';
    if (offline > 0) {
        cls = 'offline';
        text = `${offline} device${offline === 1 ? '' : 's'} down`;
    } else if (typeof health === 'number') {
        cls = 'online';
        text = 'All devices up';
    }

    dot.className = `status-dot ${cls}`;
    label.textContent = text;
}

async function loadNetworkSummary() {
    try {
        const response = await fetch(`${API_BASE}/network/summary`);
        if (!response.ok) throw new Error('Failed to fetch summary');
        const summary = await response.json();
        updateNetworkSummary(summary);
        updateNetworkStatusIndicator(summary);
    } catch (error) {
        console.error('Error loading network summary:', error);
    }
}

async function loadAlerts(unacknowledgedOnly = false) {
    try {
        const url = `${API_BASE}/alerts${unacknowledgedOnly ? '?unacknowledged=true' : ''}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error('Failed to fetch alerts');
        const nextAlerts = await response.json();
        const previousIds = (state.alerts || []).map(alert => alert.id);
        state.alerts = nextAlerts;
        displayAlerts();
        updateAlertCount();
        const newAlerts = nextAlerts.filter(alert => !previousIds.includes(alert.id));
        if (newAlerts.length > 0 && state.currentUser?.authenticated) {
            showMessage(`New alert received: ${newAlerts[0].message}`, newAlerts[0].severity === 'critical' ? 'error' : 'info');
        }
    } catch (error) {
        console.error('Error loading alerts:', error);
    }
}

// ==================== NOTIFICATIONS ====================
async function loadNotifications() {
    try {
        const response = await fetch(`${API_BASE}/notifications`);
        if (!response.ok) throw new Error('Failed to fetch notifications');
        const notes = await response.json();
        state.notifications = notes;

        // Find new notifications
        const newNotes = notes.filter(n => !state._seenNotificationIds.has(n.id));
        newNotes.forEach(n => state._seenNotificationIds.add(n.id));

        // On first load everything is "new"; announcing the backlog would bury the
        // user in toasts for events they have already lived through.
        if (!state.notificationsPrimed) {
            state.notificationsPrimed = true;
            return;
        }

        // One toast for the batch, not one per device — a failed switch produces
        // a notification per device behind it.
        if (newNotes.length === 1) {
            showToast(newNotes[0].message, newNotes[0].severity === 'critical' ? 'error' : 'info');
        } else if (newNotes.length > 1) {
            const critical = newNotes.filter(n => n.severity === 'critical').length;
            showToast(
                `${newNotes.length} new notifications${critical ? `, ${critical} critical` : ''}`,
                critical ? 'error' : 'info'
            );
        }
    } catch (error) {
        console.error('Error loading notifications:', error);
    }
}

async function loadNotificationsPanel() {
    try {
        const resp = await fetch(`${API_BASE}/notifications`);
        if (!resp.ok) throw new Error('Failed to fetch notifications');
        const notes = await resp.json();
        const container = document.getElementById('notificationsList');
        if (!container) return;
        if (!notes || notes.length === 0) {
            renderInto('notifications', container, '<p class="loading">No notifications</p>');
            return;
        }
        renderInto('notifications', container, notes.map(n => `
            <div class="notification-row ${n.severity}">
                <div style="flex:1">
                    <div class="notif-title">${escapeHtml(n.title || '')}</div>
                    <div class="notif-message">${escapeHtml(n.message)}</div>
                    <div class="notif-time">${n.created_at}</div>
                </div>
                <div style="margin-left:12px">
                    ${n.is_acknowledged ? '<span style="color:#999">Acknowledged</span>' : `<button class="btn btn-secondary" onclick="ackNotification(${n.id})">Acknowledge</button>`}
                </div>
            </div>
        `).join(''));
    } catch (err) {
        console.error('Error loading notifications panel:', err);
    }
}

async function ackNotification(notificationId) {
    try {
        const resp = await fetch(`${API_BASE}/notifications/${notificationId}/ack`, { method: 'POST' });
        if (!resp.ok) throw new Error('Failed to acknowledge');
        showMessage('Notification acknowledged', 'success');
        loadNotificationsPanel();
    } catch (err) {
        showMessage(`Error: ${err.message}`, 'error');
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
// A missing measurement is shown as a dash, not as 0 or 100 — the dashboard
// should never present an absent reading as a good one.
function formatMs(value) {
    return typeof value === 'number' ? `${value.toFixed(2)}ms` : '—';
}

function formatPercent(value) {
    return typeof value === 'number' ? `${value.toFixed(1)}%` : '—';
}

function updateNetworkSummary(summary) {
    document.getElementById('totalDevices').textContent = summary.total_devices;
    document.getElementById('onlineCount').textContent = summary.online_devices;
    document.getElementById('offlineCount').textContent = summary.offline_devices;
    document.getElementById('networkHealth').textContent = formatPercent(summary.network_health_percentage);
    document.getElementById('avgLatency').textContent = formatMs(summary.average_latency_ms);
}

function updateAlertCount() {
    const unacknowledged = state.alerts.filter(a => !a.is_acknowledged).length;
    document.getElementById('alertCount').textContent = unacknowledged;
}

function displayDevices() {
    const visibleDevices = getFilteredDevices();

    if (visibleDevices.length === 0) {
        renderInto('devices', devicesList,
            '<p class="loading">No devices match the current filters.</p>');
        return;
    }

    // Which cards exist and what is written on them permanently. A change here
    // means the list has to be rebuilt.
    const structure = visibleDevices.map(d => [
        d.id, d.name, d.ip_address, d.device_type, d.parent_id, d.group_name, state.isAdmin
    ]);

    // Readings that move on every poll — uptime counts up whether or not anything
    // happened. Rebuilding whole cards for those is what kept the list flickering,
    // so when only these differ the existing cards are updated in place.
    if (patchDeviceCards(visibleDevices, structure)) return;

    // Values are escaped and actions are wired through data attributes rather than
    // inline onclick, so a stored payload in a device field cannot break out into
    // an attribute context.
    renderInto('devices', devicesList, visibleDevices.map(device => {
        const view = topoState(device);
        return `
        <div class="device-card ${escapeHtml(view.state)}" data-device="${device.id}">
            <div class="device-header">
                <span class="device-icon">${deviceIcon(device.device_type, 26)}</span>
                <span>
                    <span class="device-name">${escapeHtml(device.name)}</span><br>
                    <span class="device-type-label">${escapeHtml(view.typeLabel)}</span>
                </span>
                <span class="status-badge ${escapeHtml(view.state)}" data-field="badge">${escapeHtml(view.label)}</span>
            </div>
            <div data-field="blocked">${view.blockedBy ? `
                <div class="device-blocked-note">Unreachable behind ${escapeHtml(view.blockedBy)} — fix that first.</div>
            ` : ''}</div>
            <dl class="device-info">
                <dt>Address</dt><dd>${escapeHtml(device.ip_address)}</dd>
                <dt>Uplink</dt><dd>${escapeHtml(uplinkName(device))}</dd>
                <dt>Group</dt><dd>${device.group_name ? escapeHtml(device.group_name) : '—'}</dd>
                <dt>Uptime</dt><dd data-field="uptime">${escapeHtml(formatUptime(device))}</dd>
            </dl>
            <div class="device-latency" data-field="latency">${
                typeof device.last_latency_ms === 'number'
                    ? `${device.last_latency_ms.toFixed(2)} ms last reply` : ''
            }</div>
            <div class="device-actions">
                <button class="btn btn-secondary" data-action="details" data-id="${device.id}">Details</button>
                <button class="btn btn-secondary" data-action="ping" data-id="${device.id}">Ping Test</button>
                ${state.isAdmin ? `
                    <button class="btn btn-secondary" data-action="edit" data-id="${device.id}">Edit</button>
                    <button class="btn btn-danger" data-action="delete" data-id="${device.id}">Delete</button>
                ` : ''}
            </div>
        </div>`;
    }).join(''));

    devicesList.dataset.structure = JSON.stringify(structure);
}

/* Update the live readings on cards that are already on screen. Returns true when
   the list needed no rebuilding, which is the normal case between status changes.
   Nothing is removed or re-created, so hover, selection and scroll all survive. */
function patchDeviceCards(devices, structure) {
    if (devicesList.dataset.structure !== JSON.stringify(structure)) return false;

    devices.forEach(device => {
        const card = devicesList.querySelector(`[data-device="${device.id}"]`);
        if (!card) return;

        const view = topoState(device);
        setIfChanged(card.querySelector('[data-field="uptime"]'), formatUptime(device));
        setIfChanged(card.querySelector('[data-field="latency"]'),
            typeof device.last_latency_ms === 'number'
                ? `${device.last_latency_ms.toFixed(2)} ms last reply` : '');

        const badge = card.querySelector('[data-field="badge"]');
        setIfChanged(badge, view.label);
        if (badge) badge.className = `status-badge ${view.state}`;

        const wanted = `device-card ${view.state}`;
        if (card.className !== wanted) card.className = wanted;

        const blocked = card.querySelector('[data-field="blocked"]');
        const note = view.blockedBy
            ? `Unreachable behind ${escapeHtml(view.blockedBy)} — fix that first.` : '';
        if (blocked && blocked.dataset.note !== note) {
            blocked.dataset.note = note;
            blocked.innerHTML = note ? `<div class="device-blocked-note">${note}</div>` : '';
        }
    });
    return true;
}

function setIfChanged(element, text) {
    if (element && element.textContent !== text) element.textContent = text;
}

/* Reachability as the map sees it: a device behind a failed switch is cut off,
   not independently broken. */
function topoState(device) {
    const node = (state.topology?.nodes || []).find(n => n.id === device.id);
    const derived = node?.derived_status || device.status;
    return {
        state: derived,
        label: derived === 'unreachable' ? 'CUT OFF' : derived.toUpperCase(),
        typeLabel: node?.type_label || 'Device',
        blockedBy: node?.blocked_by?.name || null
    };
}

function uplinkName(device) {
    if (device.parent_id === null || device.parent_id === undefined) return 'Not placed';
    const parent = state.devices.find(d => d.id === device.parent_id);
    return parent ? parent.name : `#${device.parent_id}`;
}

// Uptime is a real measurement now. Null means the device has never been polled —
// which is not the same as 100%, the value this used to invent.
function formatUptime(device) {
    if (typeof device.uptime_percentage !== 'number') return 'Not yet measured';
    return `${device.uptime_percentage.toFixed(1)}% (${device.successful_requests}/${device.total_requests})`;
}

/* The toggle shows which of the three options is active, including "match system".
   Charts read their colours from the stylesheet, so they are rebuilt on a change. */
/* Password change, plus the standing warning while the published default is still
   in use. The warning is not dismissible — it stops when the password changes. */
function setupAccountControls() {
    const modal = document.getElementById('accountModal');
    const openBtn = document.getElementById('openAccountBtn');
    const form = document.getElementById('passwordForm');

    if (openBtn && modal) {
        openBtn.addEventListener('click', () => { modal.style.display = 'block'; });
    }

    if (form) form.addEventListener('submit', handleChangePassword);

    loadSecurityWarnings();
}

async function handleChangePassword(event) {
    event.preventDefault();
    const box = document.getElementById('passwordMessage');
    const next = document.getElementById('newPassword').value;
    const confirm = document.getElementById('confirmPassword').value;

    const show = (text, kind) => {
        box.textContent = text;
        box.className = `message ${kind}`;
    };

    if (next !== confirm) {
        show('The two new passwords do not match.', 'error');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/account/password`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                current_password: document.getElementById('currentPassword').value,
                new_password: next
            })
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'Could not change password');

        show('Password updated.', 'success');
        event.target.reset();
        loadSecurityWarnings();
    } catch (error) {
        show(error.message, 'error');
    }
}

async function loadSecurityWarnings() {
    const bar = document.getElementById('securityBar');
    if (!bar) return;
    try {
        const response = await fetch(`${API_BASE}/account/security`);
        if (!response.ok) return;
        const { warnings = [] } = await response.json();

        renderInto('security', bar, warnings.map(w => `
            <div class="incident-row">
                <span class="incident-icon">${deviceIcon('alert', 20)}</span>
                <span class="incident-text">${escapeHtml(w.message)}</span>
                <button class="btn btn-sm" data-open-account="1">Change it</button>
            </div>
        `).join(''));

        bar.querySelector('[data-open-account]')?.addEventListener('click', () => {
            document.getElementById('accountModal').style.display = 'block';
        });
    } catch (error) {
        console.error('Error loading security warnings:', error);
    }
}

function setupThemeToggle() {
    const buttons = document.querySelectorAll('[data-theme-choice]');
    if (!buttons.length || !window.NetmonTheme) return;

    const paint = () => {
        const active = window.NetmonTheme.current;
        buttons.forEach(btn => {
            btn.setAttribute('aria-pressed', String(btn.dataset.themeChoice === active));
        });
    };

    buttons.forEach(btn => {
        btn.addEventListener('click', () => {
            window.NetmonTheme.set(btn.dataset.themeChoice);
        });
    });

    document.addEventListener('themechange', () => {
        paint();
        Object.values(state.charts).forEach(chart => chart && chart.destroy());
        state.charts = {};
        invalidateRender();   // every colour changed; redraw everything
        updateCharts();
        if (state.topology) renderTopology(state.topology);
        displayDevices();
    });

    // Older browsers expose addListener instead of addEventListener here, and some
    // environments have no matchMedia at all. Following the system theme is a nicety;
    // it must not be able to take the rest of the interface down with it.
    const query = window.matchMedia?.('(prefers-color-scheme: dark)');
    const onSystemChange = () => {
        if (window.NetmonTheme.current === 'system') updateCharts();
    };
    if (query?.addEventListener) query.addEventListener('change', onSystemChange);
    else if (query?.addListener) query.addListener(onSystemChange);

    paint();
}

function handleTopologyClick(event) {
    const node = event.target.closest('.topo-node');
    if (!node) return;
    event.preventDefault();
    viewDetails(Number(node.dataset.deviceId));
}

function handleDeviceCardClick(event) {
    const button = event.target.closest('[data-action]');
    if (!button || !devicesList.contains(button)) return;
    const id = Number(button.dataset.id);
    const device = state.devices.find(d => d.id === id);
    if (!device) return;

    switch (button.dataset.action) {
        case 'details': viewDetails(id); break;
        case 'ping':    manualPing(device.ip_address, id); break;
        case 'edit':    editDevice(id); break;
        case 'delete':  deleteDeviceConfirm(id); break;
    }
}

// Hide controls the server will reject anyway. Operators previously saw Add, Edit,
// Delete and Start Monitoring buttons that failed with 403 when clicked.
function applyRolePermissions() {
    if (state.isAdmin) return;
    document.querySelectorAll(
        '.add-device-section, #createGroupBtn, .monitoring-section .monitoring-controls, ' +
        '.monitoring-section .monitoring-interval, #saveGatewayBtn, #networkGatewayInput'
    ).forEach(el => { el.style.display = 'none'; });
}

function displayGroups() {
    const groupsList = document.getElementById('groupsList');
    if (state.groups.length === 0) {
        renderInto('groups', groupsList, '<p class="loading">No groups created yet</p>');
        return;
    }

    renderInto('groups', groupsList, state.groups.map(group => {
        const deviceCount = state.devices.filter(d => d.group_name === group.name).length;
        return `
            <div class="group-card" style="border-left-color: ${escapeHtml(group.color || 'var(--accent)')}">
                <div class="group-name">${escapeHtml(group.name)}</div>
                <div class="group-description">${escapeHtml(group.description || '')}</div>
                <div class="group-count">${deviceCount} devices</div>
            </div>
        `;
    }).join(''));
}

function updateGroupedDevices() {
    const container = document.getElementById('groupedDevices');
    
    if (state.devices.length === 0) {
        renderInto('grouped', container, '<p class="loading">No devices to display</p>');
        return;
    }

    const grouped = {};
    state.devices.forEach(device => {
        const group = device.group_name || 'Ungrouped';
        if (!grouped[group]) grouped[group] = [];
        grouped[group].push(device);
    });

    renderInto('grouped', container, Object.entries(grouped).map(([groupName, devices]) => {
        const onlineCount = devices.filter(d => d.status === 'online').length;
        const healthPercent = Math.round(onlineCount / devices.length * 100);

        return `
            <div class="group-section">
                <h4>${escapeHtml(groupName)} <span style="color: #999; font-size: 0.9em;">(${onlineCount}/${devices.length} online - ${healthPercent}%)</span></h4>
                <div class="group-device-list">
                    ${devices.map(d => `
                        <div class="group-device-item">
                            <span class="status-dot ${escapeHtml(d.status)}"></span>
                            <div class="group-device-name">${escapeHtml(d.name)}</div>
                            <span class="device-latency" style="margin-left:auto">${
                                typeof d.last_latency_ms === 'number' ? d.last_latency_ms.toFixed(1) + ' ms' : '—'
                            }</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }).join(''));
}

function displayStatistics(stats) {
    const container = document.getElementById('statisticsList');
    
    renderInto('statistics', container, stats.map(stat => `
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
                <span class="stat-value">${formatPercent(stat.uptime_percentage)}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Avg Latency</span>
                <span class="stat-value">${formatMs(stat.avg_latency_ms)}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Min Latency</span>
                <span class="stat-value">${formatMs(stat.min_latency_ms)}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Max Latency</span>
                <span class="stat-value">${formatMs(stat.max_latency_ms)}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Status Changes</span>
                <span class="stat-value">${stat.status_changes}</span>
            </div>
        </div>
    `).join(''));
}

function displayTimeline(timeline) {
    const container = document.getElementById('timelineContainer');
    
    if (timeline.length === 0) {
        renderInto('timeline', container, '<p class="loading">No status changes recorded</p>');
        return;
    }

    renderInto('timeline', container, `
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
    `);
}

function displayAlerts() {
    const container = document.getElementById('alertsList');
    
    if (state.alerts.length === 0) {
        renderInto('alerts', container, '<p class="loading">No alerts</p>');
        return;
    }

    renderInto('alerts', container, state.alerts.map(alert => `
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
    `).join(''));
}

// ==================== DEVICE MANAGEMENT ====================
async function handleDeviceFormSubmit(e) {
    e.preventDefault();

    const parentValue = document.getElementById('deviceParent').value;
    const payload = {
        name: document.getElementById('deviceName').value.trim(),
        ip_address: document.getElementById('deviceIP').value.trim(),
        interval: parseInt(document.getElementById('deviceInterval').value, 10),
        timeout: parseInt(document.getElementById('deviceTimeout').value, 10),
        device_type: document.getElementById('deviceType').value,
        parent_id: parentValue ? parseInt(parentValue, 10) : null,
        group_name: document.getElementById('deviceGroup').value || null
    };

    if (!payload.name || !payload.ip_address) {
        showFormMessage('Enter a name and an address for the device.', 'error');
        return;
    }

    const editing = state.editingDeviceId !== null;
    const submit = document.getElementById('deviceFormSubmit');
    submit.disabled = true;

    try {
        const response = await fetch(
            editing ? `${API_BASE}/devices/${state.editingDeviceId}` : `${API_BASE}/devices`,
            {
                method: editing ? 'PUT' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }
        );
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'Could not save the device');

        // Editing sets the group in the same PUT; a new device needs the follow-up
        // call because it did not exist a moment ago.
        if (!editing && payload.group_name) {
            await fetch(`${API_BASE}/devices/${body.id}/assign-group`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ group_name: payload.group_name })
            });
        }

        const label = payload.name;
        closeDeviceForm();
        await loadAllData();
        showToast(editing ? `${label} updated` : `${label} added to the network map`, 'success');
    } catch (error) {
        showFormMessage(error.message, 'error');
    } finally {
        submit.disabled = false;
    }
}

/* One dialog, two modes. Edit previously ran a chain of browser prompt() boxes,
   which could not show the device type or its uplink at all. */
function openDeviceForm(device = null) {
    const modal = document.getElementById('deviceFormModal');
    if (!modal) return;

    state.editingDeviceId = device ? device.id : null;
    // The dialog is about to open, so refresh its options now — the poll will not
    // touch them again until it closes.
    invalidateRender('groupOptions');
    populateGroupSelect();

    document.getElementById('deviceFormTitle').textContent =
        device ? `Edit ${device.name}` : 'Add device';
    document.getElementById('deviceFormSubmit').textContent =
        device ? 'Save changes' : 'Add device';
    document.getElementById('deviceFormIntro').textContent = device
        ? 'Changing the uplink moves this device on the network map.'
        : 'Pick what the device is and what it plugs into. That uplink is what lets the map show where a fault actually is.';

    document.getElementById('deviceName').value = device ? device.name : '';
    document.getElementById('deviceIP').value = device ? device.ip_address : '';
    document.getElementById('deviceInterval').value = device ? device.interval : 5;
    document.getElementById('deviceTimeout').value = device ? device.timeout : 2;

    // Populate the type list first, then select — the uplink options depend on it.
    syncDeviceFormOptions();
    document.getElementById('deviceType').value = device ? device.device_type : 'switch';
    syncDeviceFormOptions();

    document.getElementById('deviceParent').value =
        device && device.parent_id !== null && device.parent_id !== undefined
            ? String(device.parent_id) : '';
    document.getElementById('deviceGroup').value = device?.group_name || '';

    showFormMessage('');
    modal.style.display = 'block';
    document.getElementById('deviceName').focus();
}

function closeDeviceForm() {
    const modal = document.getElementById('deviceFormModal');
    if (modal) modal.style.display = 'none';
    state.editingDeviceId = null;
    showFormMessage('');
    flushPendingToasts();
    loadAllData();
}

function editDevice(deviceId) {
    const device = state.devices.find(d => d.id === deviceId);
    if (device) openDeviceForm(device);
}

async function viewDetails(deviceId) {
    const device = state.devices.find(d => d.id === deviceId);
    if (!device) return;

    const stats = await fetch(`${API_BASE}/devices/${deviceId}/statistics`).then(r => r.json());
    const lastSeen = device.last_seen_at ? new Date(device.last_seen_at).toLocaleString() : 'Never';

    const modalBody = document.getElementById('modalBody');
    // Written directly, not through the guard: this is an explicit click, and the
    // dialog should always show the figures as of the moment it was opened.
    modalBody.innerHTML = `
        <h2 style="margin-bottom: 20px;">${escapeHtml(device.name)}</h2>
        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px;">
            <div>
                <p><strong>IP Address:</strong> ${escapeHtml(device.ip_address)}</p>
                <p><strong>Group:</strong> ${device.group_name ? escapeHtml(device.group_name) : 'Ungrouped'}</p>
                <p><strong>Status:</strong> <span class="status-badge ${escapeHtml(device.status)}">${escapeHtml(device.status).toUpperCase()}</span></p>
                <p><strong>Last Latency:</strong> ${formatMs(device.last_latency_ms)}</p>
                <p><strong>Last Seen:</strong> ${escapeHtml(lastSeen)}</p>
            </div>
            <div>
                <p><strong>Interval:</strong> ${Number(device.interval)}s</p>
                <p><strong>Timeout:</strong> ${Number(device.timeout)}s</p>
                <p><strong>Uptime:</strong> ${formatPercent(stats.uptime_percentage)}</p>
                <p><strong>Total Pings:</strong> ${stats.total_pings}</p>
            </div>
        </div>
        <div style="margin-top: 20px;">
            <p><strong>Performance Metrics (24h)</strong></p>
            <p>Avg Latency: ${formatMs(stats.avg_latency_ms)} | Min: ${formatMs(stats.min_latency_ms)} | Max: ${formatMs(stats.max_latency_ms)}</p>
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

/* Rebuilding a <select> discards the current selection and shuts an open dropdown.
   Doing that on every poll made the group field reset itself while it was being
   used, so the options are only rewritten when the group list has actually
   changed — and never underneath an open dialog. */
function populateGroupSelect() {
    const names = state.groups.map(g => g.name);

    // Never rebuilt under an open dialog: the field is in use there.
    if (!isModalOpen()) {
        renderOptions('groupOptions', document.getElementById('deviceGroup'),
            names.map(name => ({ value: name, label: name })),
            { placeholder: { value: '', label: 'No group' } });
    }

    renderOptions('groupFilterOptions', document.getElementById('deviceGroupFilter'),
        ['Ungrouped', ...names].map(name => ({ value: name, label: name })),
        { placeholder: { value: '', label: 'All groups' } });
}


function populateTimelineSelect() {
    const select = document.getElementById('timelineDeviceSelect');
    renderOptions('timelineDevices', select,
        state.devices.map(d => ({ value: String(d.id), label: d.name })),
        { placeholder: { value: '', label: 'Select a device' } });
}

// ==================== USERS (ADMIN) ====================
async function loadUsersPanel() {
    try {
        const resp = await fetch(`${API_BASE}/users`);
        if (!resp.ok) throw new Error('Failed to fetch users');
        const users = await resp.json();
        const panel = document.getElementById('usersPanel');
        if (!panel) return;
        renderInto('users', panel, users.map(u => `
            <div class="user-row">
                <div class="user-info">
                    <strong>${escapeHtml(u.username)}</strong> <span style="color:#666;margin-left:8px;">${escapeHtml(u.display_name||'')}</span>
                    <div style="font-size:0.9em;color:#777;">Last login: ${u.last_login_at || 'Never'}</div>
                </div>
                <div class="user-actions">
                    <select data-user-id="${u.id}" class="role-select">
                        <option value="admin" ${u.role==='admin'?'selected':''}>Admin</option>
                        <option value="network_engineer" ${u.role==='network_engineer'?'selected':''}>Network Engineer</option>
                        <option value="operator" ${u.role==='operator'?'selected':''}>Operator</option>
                        <option value="viewer" ${u.role==='viewer'?'selected':''}>Viewer</option>
                    </select>
                    <button class="btn btn-secondary" onclick="changeUserRole(${u.id})">Save</button>
                </div>
            </div>
        `).join(''));

        // attach change handlers
        document.querySelectorAll('.role-select').forEach(sel => {
            sel.addEventListener('change', (e)=>{
                const id = parseInt(e.target.dataset.userId);
                // highlight save button or auto-save
            });
        });
    } catch (err) {
        console.error('Error loading users:', err);
    }
}

async function changeUserRole(userId) {
    try {
        const select = document.querySelector(`select[data-user-id="${userId}"]`);
        if (!select) return;
        const role = select.value;
        const resp = await fetch(`${API_BASE}/users/${userId}/role`, {
            method: 'PUT',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({role})
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.error||'Failed to update role');
        }
        showMessage('Role updated', 'success');
        loadUsersPanel();
    } catch (err) {
        showMessage(`Error: ${err.message}`, 'error');
    }
}

// ==================== MANUAL PING ====================
async function manualPing(ipAddress, deviceId) {
    try {
        const response = await fetch(`${API_BASE}/ping/${encodeURIComponent(ipAddress)}`, {
            method: 'POST'
        });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Ping failed');
        }

        const result = await response.json();
        showMessage(`Ping ${ipAddress}: ${result.status.toUpperCase()} ${result.latency_ms ? result.latency_ms + 'ms' : ''}`, result.status === 'online' ? 'success' : 'error');
        await loadAllData();
        return result;
    } catch (error) {
        showMessage(`Error: ${error.message}`, 'error');
    }
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

function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartTheme() {
    return {
        up: cssVar('--up'),
        down: cssVar('--down'),
        blocked: cssVar('--blocked'),
        unknown: cssVar('--unknown'),
        accent: cssVar('--accent'),
        ink: cssVar('--ink-2'),
        grid: cssVar('--line')
    };
}

function updateStatusChart() {
    const statusCanvas = document.getElementById('statusChart');
    
    const t = chartTheme();
    // Counted from the map's view, so devices cut off by a failed uplink are not
    // lumped in with devices that failed on their own.
    const nodes = state.topology?.nodes || [];
    const count = (s) => nodes.length
        ? nodes.filter(n => n.derived_status === s).length
        : state.devices.filter(d => d.status === s).length;

    const slices = [
        ['Responding', count('online'), t.up],
        ['Down', count('offline'), t.down],
        ['Cut off', count('unreachable'), t.blocked],
        ['Not polled', count('unknown'), t.unknown]
    ].filter(([, value]) => value > 0);

    if (state.charts.status) {
        const chart = state.charts.status;
        chart.data.labels = slices.map(s => s[0]);
        chart.data.datasets[0].data = slices.map(s => s[1]);
        chart.data.datasets[0].backgroundColor = slices.map(s => s[2]);
        chart.data.datasets[0].borderColor = cssVar('--panel');
        chart.options.plugins.legend.labels.color = t.ink;
        chart.update();
        return;
    }

    state.charts.status = new Chart(statusCanvas, {
        type: 'doughnut',
        data: {
            labels: slices.map(s => s[0]),
            datasets: [{
                data: slices.map(s => s[1]),
                backgroundColor: slices.map(s => s[2]),
                borderColor: cssVar('--panel'),
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '62%',
            plugins: {
                legend: { position: 'bottom', labels: { color: t.ink, boxWidth: 10, padding: 14 } },
                title: { display: false }
            }
        }
    });
}

function updateLatencyChart() {
    const latencyCanvas = document.getElementById('latencyChart');
    
    const t = chartTheme();
    // Only devices that actually answered have a latency to plot; the rest would
    // otherwise draw a zero-length bar reading as an instant response.
    const responding = state.devices
        .filter(d => typeof d.last_latency_ms === 'number' && d.status === 'online')
        .sort((a, b) => b.last_latency_ms - a.last_latency_ms)
        .slice(0, 12);

    if (state.charts.latency) {
        const chart = state.charts.latency;
        chart.data.labels = responding.map(d => d.name.substring(0, 20));
        chart.data.datasets[0].data = responding.map(d => d.last_latency_ms);
        chart.data.datasets[0].backgroundColor = t.accent;
        chart.options.scales.x.ticks.color = t.ink;
        chart.options.scales.x.grid.color = t.grid;
        chart.options.scales.y.ticks.color = t.ink;
        chart.update();
        return;
    }

    state.charts.latency = new Chart(latencyCanvas, {
        type: 'bar',
        data: {
            labels: responding.map(d => d.name.substring(0, 20)),
            datasets: [{
                label: 'Last reply (ms)',
                data: responding.map(d => d.last_latency_ms),
                backgroundColor: t.accent,
                borderWidth: 0,
                borderRadius: 2,
                barThickness: 13
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                legend: { display: false },
                title: { display: false }
            },
            scales: {
                x: { beginAtZero: true, ticks: { color: t.ink }, grid: { color: t.grid } },
                y: { ticks: { color: t.ink }, grid: { display: false } }
            }
        }
    });
}

// ==================== UTILITY FUNCTIONS ====================
function showMessage(text, type) {
    showToast(text, type);
}

/* Result of something the user just did, shown inside the form they did it in. */
function showFormMessage(text, type) {
    if (!formMessage) return;
    formMessage.textContent = text || '';
    formMessage.className = text ? `message ${type}` : 'message';
}

/* Something that happened on the network. Never touches form fields, and waits
   rather than appearing over an open dialog. */
function showToast(text, type) {
    const banner = document.getElementById('notificationBanner');
    if (!banner) return;

    if (isModalOpen()) {
        state.pendingToasts.push({ text, type });
        return;
    }

    banner.textContent = text;
    banner.className = `notification-banner show ${type}`;
    clearTimeout(showToast.timeoutId);
    showToast.timeoutId = setTimeout(() => {
        banner.className = 'notification-banner';
    }, 4500);
}

/* One way out of every dialog: the ×, a Cancel button, the backdrop, or Escape. */
function closeModal(element) {
    if (!element) return;
    if (element.id === 'deviceFormModal') {
        closeDeviceForm();
        return;
    }
    element.style.display = 'none';
    flushPendingToasts();
}

function isModalOpen() {
    return [...document.querySelectorAll('.modal')]
        .some(m => m.style.display === 'block');
}

function flushPendingToasts() {
    const queued = state.pendingToasts.splice(0);
    if (!queued.length) return;
    const worst = queued.find(t => t.type === 'error') || queued[queued.length - 1];
    const extra = queued.length > 1 ? ` (+${queued.length - 1} more)` : '';
    showToast(worst.text + extra, worst.type);
}

function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return String(text).replace(/[&<>"']/g, m => map[m]);
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
        const intervalValue = parseInt(document.getElementById('monitoringInterval').value, 10) || 5;
        const response = await fetch(`${API_BASE}/monitoring/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ interval: intervalValue })
        });
        
        if (!response.ok) throw new Error('Failed to start monitoring');
        
        const result = await response.json();
        showMessage(`Automatic ping monitoring started at ${intervalValue}s interval! ✓`, 'success');
        updateMonitoringUI(true);
        
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

async function saveNetworkGateway() {
    try {
        const input = document.getElementById('networkGatewayInput');
        if (!input) return;
        const gatewayIp = input.value.trim();
        const response = await fetch(`${API_BASE}/settings/network-gateway`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gateway_ip: gatewayIp })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Failed to save network gateway');
        const status = document.getElementById('gatewayStatus');
        if (status) {
            status.textContent = `Gateway saved: ${data.gateway_ip}`;
        }
        showMessage(`Gateway updated to ${data.gateway_ip}`, 'success');
    } catch (error) {
        showMessage(`Error: ${error.message}`, 'error');
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
