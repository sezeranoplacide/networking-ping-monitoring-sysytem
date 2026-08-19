/* ============================================================================
   Diagnostics scratchpad.

   The second problem this system exists to solve: engineers retyping the same
   handful of commands into a terminal and reading the output by eye. Every check
   here is one click, runs on the server, and reports a plain-language conclusion
   alongside the raw output — with the exact command it used, so nothing has to be
   taken on trust.

   Results accumulate newest-first rather than replacing each other, because
   diagnosis is comparing one result against the last, not looking at one at a time.
   ========================================================================== */

const diag = {
    operations: [],
    canRun: false,
    gateway: '',
    entries: 0,
};

async function loadDiagnostics() {
    try {
        const response = await fetch(`${API_BASE}/diagnostics`);
        if (!response.ok) return;
        const data = await response.json();

        diag.operations = data.operations || [];
        diag.canRun = data.can_run === true;
        diag.gateway = data.gateway || '';

        renderOptions('diagDevices', document.getElementById('diagDevice'),
            (data.targets || []).map(t => ({
                value: t.address,
                label: `${t.name} · ${t.address}`,
            })),
            { placeholder: { value: '', label: 'Choose a monitored device…' } });

        renderDiagnosticOperations();
    } catch (error) {
        console.error('Error loading diagnostics:', error);
    }
}

function renderDiagnosticOperations() {
    const container = document.getElementById('diagOperations');
    if (!container) return;

    if (!diag.canRun) {
        renderInto('diagOps', container,
            '<p class="loading">Running checks needs an engineer or admin account.</p>');
        return;
    }

    // "Locate the fault" first: it is the whole point, and it runs the others in
    // the order an engineer would.
    const cards = [`
        <button type="button" class="diag-op is-primary" data-locate="1">
            <span class="diag-op-name">Locate the fault</span>
            <span class="diag-op-desc">Checks the gateway, walks the path and reads the local
                segment, then names the closest point to the problem.</span>
        </button>`];

    diag.operations.forEach(op => {
        cards.push(`
        <button type="button" class="diag-op" data-operation="${escapeHtml(op.key)}">
            <span class="diag-op-name">${escapeHtml(op.label)}</span>
            <span class="diag-op-desc">${escapeHtml(op.description)}</span>
        </button>`);
    });

    renderInto('diagOps', container, cards.join(''));
}

/* The address to act on: an explicitly typed one wins over the picked device. */
function diagnosticTarget() {
    const typed = document.getElementById('diagTarget')?.value.trim();
    if (typed) return typed;
    return document.getElementById('diagDevice')?.value || '';
}

function diagnosticPort() {
    const value = document.getElementById('diagPort')?.value.trim();
    return value ? parseInt(value, 10) : null;
}

async function runDiagnostic(operationKey) {
    const op = diag.operations.find(o => o.key === operationKey);
    if (!op) return;

    const target = op.needs_target ? diagnosticTarget() : diagnosticTarget() || null;
    if (op.needs_target && !target) {
        showToast('Choose a device or type an address first', 'error');
        return;
    }
    if (op.needs_port && !diagnosticPort()) {
        showToast(`${op.label} needs a port number`, 'error');
        return;
    }

    const entry = openLogEntry(op.label, target || 'this server');
    setOperationsBusy(true);
    try {
        const response = await fetch(`${API_BASE}/diagnostics/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                operation: operationKey,
                target: target || null,
                port: op.needs_port ? diagnosticPort() : null,
            }),
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'The check could not be completed');
        entry.innerHTML = renderResult(body);
        entry.className = `diag-entry ${body.ok ? 'ok' : 'failed'}`;
    } catch (error) {
        entry.innerHTML = renderFailure(op.label, target, error.message);
        entry.className = 'diag-entry failed';
    } finally {
        setOperationsBusy(false);
    }
}

async function locateFault() {
    const address = diagnosticTarget();
    const device = state.devices.find(d => d.ip_address === address);
    if (!device) {
        showToast('Pick a monitored device — locating a fault needs its place on the map', 'error');
        return;
    }

    const entry = openLogEntry('Locate the fault', device.name);
    setOperationsBusy(true);
    try {
        const response = await fetch(`${API_BASE}/diagnostics/locate/${device.id}`, {
            method: 'POST',
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'Could not complete the checks');

        entry.innerHTML = `
            <div class="diag-entry-head">
                <span class="diag-entry-title">Locate the fault</span>
                <span class="diag-entry-target">${escapeHtml(device.name)} · ${escapeHtml(device.ip_address)}</span>
                <span class="diag-entry-time">${new Date().toLocaleTimeString()}</span>
            </div>
            <div class="diag-verdict">${escapeHtml(body.verdict)}</div>
            ${body.steps.map(renderResult).join('')}`;
        entry.className = `diag-entry ${body.reachable ? 'ok' : 'failed'}`;
    } catch (error) {
        entry.innerHTML = renderFailure('Locate the fault', device.name, error.message);
        entry.className = 'diag-entry failed';
    } finally {
        setOperationsBusy(false);
    }
}

function openLogEntry(label, target) {
    const log = document.getElementById('diagLog');
    if (diag.entries === 0) log.innerHTML = '';
    diag.entries += 1;

    const entry = document.createElement('div');
    entry.className = 'diag-entry running';
    entry.innerHTML = `
        <div class="diag-entry-head">
            <span class="diag-entry-title">${escapeHtml(label)}</span>
            <span class="diag-entry-target">${escapeHtml(target)}</span>
            <span class="diag-entry-time">running…</span>
        </div>`;
    log.prepend(entry);
    return entry;
}

function renderResult(result) {
    const hops = result.detail?.hops;
    return `
        <div class="diag-entry-head">
            <span class="diag-entry-title">${escapeHtml(result.label)}</span>
            <span class="diag-entry-target">${escapeHtml(result.target || 'this server')}</span>
            <span class="diag-entry-time">${result.duration_ms} ms</span>
        </div>
        <div class="diag-summary">${escapeHtml(result.summary)}</div>
        ${hops && hops.length ? renderHops(hops, result.detail) : ''}
        <div class="diag-command">$ ${escapeHtml(result.command)}</div>
        ${result.output ? `
            <details class="diag-raw">
                <summary>Raw output</summary>
                <pre class="diag-output">${escapeHtml(result.output)}</pre>
            </details>
        ` : ''}`;
}

/* The hop table is the point of a trace: which one last answered, and where it
   went quiet. Reading that off raw tracert output is the tedious part. */
function renderHops(hops, detail) {
    const lastGood = detail?.last_responding_hop?.hop;
    return `
        <div style="overflow-x:auto;padding:0 13px 10px">
            <table class="diag-hops">
                <thead><tr><th>Hop</th><th>Address</th><th>Round trip</th></tr></thead>
                <tbody>
                    ${hops.map(h => `
                        <tr class="${h.timed_out ? 'silent' : ''} ${h.hop === lastGood ? 'last-good' : ''}">
                            <td>${h.hop}</td>
                            <td>${h.address ? escapeHtml(h.address) : 'no reply'}</td>
                            <td>${h.rtt_ms.length ? h.rtt_ms.map(t => `${t} ms`).join(', ') : '—'}</td>
                        </tr>`).join('')}
                </tbody>
            </table>
        </div>`;
}

function renderFailure(label, target, message) {
    return `
        <div class="diag-entry-head">
            <span class="diag-entry-title">${escapeHtml(label)}</span>
            <span class="diag-entry-target">${escapeHtml(target || '')}</span>
            <span class="diag-entry-time">${new Date().toLocaleTimeString()}</span>
        </div>
        <div class="diag-summary">${escapeHtml(message)}</div>`;
}

function setOperationsBusy(busy) {
    document.querySelectorAll('.diag-op').forEach(btn => { btn.disabled = busy; });
}

function setupDiagnostics() {
    const operations = document.getElementById('diagOperations');
    if (operations) {
        operations.addEventListener('click', (e) => {
            const button = e.target.closest('.diag-op');
            if (!button || button.disabled) return;
            if (button.dataset.locate) locateFault();
            else runDiagnostic(button.dataset.operation);
        });
    }

    document.getElementById('diagClear')?.addEventListener('click', () => {
        diag.entries = 0;
        document.getElementById('diagLog').innerHTML =
            '<p class="loading">Pick a target, then run a check. Results collect here, newest first.</p>';
    });

    // Picking a device clears a typed address, so the target is never ambiguous.
    document.getElementById('diagDevice')?.addEventListener('change', () => {
        const typed = document.getElementById('diagTarget');
        if (typed) typed.value = '';
    });

    document.getElementById('detectGatewayBtn')?.addEventListener('click', detectGateway);
}

async function detectGateway() {
    try {
        const response = await fetch(`${API_BASE}/diagnostics/detect-gateway`, { method: 'POST' });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'Could not read the routing table');

        document.getElementById('networkGatewayInput').value = body.gateway_ip;
        document.getElementById('gatewayStatus').textContent =
            `Found ${body.gateway_ip} in this server's routing table. Save to keep it.`;
    } catch (error) {
        showToast(error.message, 'error');
    }
}

/* ------------------------------------------------------------------ console */

/* The console is shown only where it exists: the desktop application. Served over
   a network the endpoint refuses, so the pane stays hidden rather than offering a
   control that cannot work. */
const consoleState = { enabled: false, cwd: '', history: [], cursor: -1 };

async function loadConsole() {
    const card = document.getElementById('consoleCard');
    if (!card) return;

    try {
        const response = await fetch(`${API_BASE}/console`);
        if (!response.ok) return;
        const data = await response.json();

        consoleState.enabled = data.enabled === true && data.can_run === true;
        consoleState.cwd = data.cwd || '';

        card.hidden = !data.can_run;
        if (!card.hidden) {
            document.getElementById('consoleCwd').textContent = data.cwd || '';
            document.getElementById('consoleMode').textContent =
                data.enabled ? 'desktop session' : 'desktop only';
            document.getElementById('consoleMode').className =
                `status-badge ${data.enabled ? 'online' : 'neutral'}`;
            document.getElementById('consoleNote').textContent = data.enabled
                ? 'Runs on this machine with your own permissions. Every command is recorded in the audit log.'
                : data.reason || '';
            document.getElementById('consoleCommand').disabled = !data.enabled;
            document.getElementById('consoleRun').disabled = !data.enabled;

            renderConsoleSnippets(data.snippets || []);
        }
    } catch (error) {
        console.error('Error loading console:', error);
    }
}

function renderConsoleSnippets(sections) {
    const html = sections.map(section => `
        <span class="console-group-label">${escapeHtml(section.group)}</span>
        ${section.items.map(item => `
            <button type="button" class="console-snippet"
                    data-command="${escapeHtml(item.command)}"
                    title="${escapeHtml(item.command)}">${escapeHtml(item.label)}</button>
        `).join('')}`).join('');
    renderInto('consoleSnippets', document.getElementById('consoleSnippets'), html);
}

async function runConsoleCommand(command) {
    if (!consoleState.enabled || !command.trim()) return;

    consoleState.history.unshift(command);
    consoleState.cursor = -1;

    const entry = openLogEntry(command, consoleState.cwd);
    entry.classList.add('console');
    try {
        const response = await fetch(`${API_BASE}/console/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command }),
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'The command could not be run');

        entry.innerHTML = `
            <div class="diag-entry-head">
                <span class="diag-entry-title">${escapeHtml(body.command)}</span>
                <span class="diag-exit ${body.exit_code === 0 ? 'zero' : 'nonzero'}">exit ${body.exit_code}</span>
                <span class="diag-entry-time">${body.duration_ms} ms</span>
            </div>
            ${body.timed_out ? '<div class="diag-summary">Stopped — it ran past the time limit.</div>' : ''}
            <pre class="diag-output">${escapeHtml(body.output || '(no output)')}</pre>`;
        entry.className = `diag-entry console ${body.ok ? 'ok' : 'failed'}`;
    } catch (error) {
        entry.innerHTML = renderFailure(command, consoleState.cwd, error.message);
        entry.className = 'diag-entry console failed';
    }
}

function setupConsole() {
    const form = document.getElementById('consoleForm');
    const input = document.getElementById('consoleCommand');
    if (!form || !input) return;

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const command = input.value;
        input.value = '';
        runConsoleCommand(command);
    });

    // Up and down walk previous commands, the way a shell does.
    input.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
        if (!consoleState.history.length) return;
        e.preventDefault();

        if (e.key === 'ArrowUp') {
            consoleState.cursor = Math.min(consoleState.cursor + 1, consoleState.history.length - 1);
        } else {
            consoleState.cursor = Math.max(consoleState.cursor - 1, -1);
        }
        input.value = consoleState.cursor >= 0 ? consoleState.history[consoleState.cursor] : '';
    });

    // A palette entry fills the box rather than running immediately, so it can be
    // edited first — most of these need a target substituted.
    document.getElementById('consoleSnippets')?.addEventListener('click', (e) => {
        const snippet = e.target.closest('.console-snippet');
        if (!snippet) return;
        input.value = snippet.dataset.command;
        input.focus();
    });
}
