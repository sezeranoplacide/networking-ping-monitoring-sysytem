/* ============================================================================
   Network map — device iconography and topology rendering.

   The dashboard used to be an unordered list of addresses, which is why a failed
   switch produced one alert per device behind it. Devices now declare an uplink,
   so the network can be drawn as it is actually wired and a failure attributed to
   one point instead of forty.
   ========================================================================== */

/* ============================================================================
   Device iconography — Lucide (ISC licensed), bundled rather than linked.

   These are the real icon set, not hand-drawn shapes; they are served from this
   application's own static directory because a CDN is unreachable exactly when
   the monitored network is impaired, which is the failure this tool exists to
   diagnose. See SYSTEM_AUDIT.md finding M7.

   Every glyph inherits currentColor, so device state drives the colour.
   ========================================================================== */

const ICON_ATTRS = 'fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round"';

const DEVICE_ICONS = {
    router: `<rect width="20" height="8" x="2" y="14" rx="2" /> <path d="M6.01 18H6" /> <path d="M10.01 18H10" /> <path d="M15 10v4" /> <path d="M17.84 7.17a4 4 0 0 0-5.66 0" /> <path d="M20.66 4.34a8 8 0 0 0-11.31 0" />`,
    switch: `<rect x="16" y="16" width="6" height="6" rx="1" /> <rect x="2" y="16" width="6" height="6" rx="1" /> <rect x="9" y="2" width="6" height="6" rx="1" /> <path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3" /> <path d="M12 12V8" />`,
    firewall: `<rect width="18" height="18" x="3" y="3" rx="2" /> <path d="M12 9v6" /> <path d="M16 15v6" /> <path d="M16 3v6" /> <path d="M3 15h18" /> <path d="M3 9h18" /> <path d="M8 15v6" /> <path d="M8 3v6" />`,
    access_point: `<path d="M12 20h.01" /> <path d="M2 8.82a15 15 0 0 1 20 0" /> <path d="M5 12.859a10 10 0 0 1 14 0" /> <path d="M8.5 16.429a5 5 0 0 1 7 0" />`,
    server: `<rect width="20" height="8" x="2" y="2" rx="2" ry="2" /> <rect width="20" height="8" x="2" y="14" rx="2" ry="2" /> <line x1="6" x2="6.01" y1="6" y2="6" /> <line x1="6" x2="6.01" y1="18" y2="18" />`,
    workstation: `<rect width="20" height="14" x="2" y="3" rx="2" /> <line x1="8" x2="16" y1="21" y2="21" /> <line x1="12" x2="12" y1="17" y2="21" />`,
    ip_phone: `<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />`,
    printer: `<path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2" /> <path d="M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6" /> <rect x="6" y="14" width="12" height="8" rx="1" />`,
    camera: `<path d="M16.75 12h3.632a1 1 0 0 1 .894 1.447l-2.034 4.069a1 1 0 0 1-1.708.134l-2.124-2.97" /> <path d="M17.106 9.053a1 1 0 0 1 .447 1.341l-3.106 6.211a1 1 0 0 1-1.342.447L3.61 12.3a2.92 2.92 0 0 1-1.3-3.91L3.69 5.6a2.92 2.92 0 0 1 3.92-1.3z" /> <path d="M2 19h3.76a2 2 0 0 0 1.8-1.1L9 15" /> <path d="M2 21v-4" /> <path d="M7 9h.01" />`,
    nas: `<line x1="22" x2="2" y1="12" y2="12" /> <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" /> <line x1="6" x2="6.01" y1="16" y2="16" /> <line x1="10" x2="10.01" y1="16" y2="16" />`,
    other: `<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" /> <path d="m3.3 7 8.7 5 8.7-5" /> <path d="M12 22V12" />`,
    alert: `<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" /> <path d="M12 9v4" /> <path d="M12 17h.01" />`,
    brand: `<path d="M4.9 16.1C1 12.2 1 5.8 4.9 1.9" /> <path d="M7.8 4.7a6.14 6.14 0 0 0-.8 7.5" /> <circle cx="12" cy="9" r="2" /> <path d="M16.2 4.8c2 2 2.26 5.11.8 7.47" /> <path d="M19.1 1.9a9.96 9.96 0 0 1 0 14.1" /> <path d="M9.5 18h5" /> <path d="m8 22 4-11 4 11" />`,
};

function deviceIcon(type, size = 24) {
    const body = DEVICE_ICONS[type] || DEVICE_ICONS.other;
    return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" ${ICON_ATTRS} ` +
           `aria-hidden="true" focusable="false">${body}</svg>`;
}

/* ------------------------------------------------------------------ layout */

const NODE_W = 148;
const NODE_H = 66;
const GAP_X = 22;
const GAP_Y = 78;

/* A tidy layered tree: depth sets the row, leaves claim the next free column and
   parents centre over their children. Wide enough estates simply scroll. */
function layoutTopology(nodes) {
    const byId = new Map(nodes.map(n => [n.id, n]));
    const children = new Map();
    const roots = [];

    nodes.forEach(node => {
        const hasParent = node.parent_id !== null && byId.has(node.parent_id);
        if (hasParent) {
            if (!children.has(node.parent_id)) children.set(node.parent_id, []);
            children.get(node.parent_id).push(node);
        } else {
            roots.push(node);
        }
    });

    // Infrastructure first, then alphabetically — the shape stays stable between
    // refreshes instead of reshuffling every five seconds.
    const order = (a, b) =>
        (b.is_infrastructure - a.is_infrastructure) || a.name.localeCompare(b.name);
    roots.sort(order);
    children.forEach(list => list.sort(order));

    const placed = new Map();
    let column = 0;

    const walk = (node, depth, seen) => {
        if (seen.has(node.id)) return null;
        seen.add(node.id);

        const kids = children.get(node.id) || [];
        let x;
        if (kids.length === 0) {
            x = column * (NODE_W + GAP_X);
            column += 1;
        } else {
            const positions = kids.map(kid => walk(kid, depth + 1, seen)).filter(v => v !== null);
            x = positions.length
                ? (Math.min(...positions) + Math.max(...positions)) / 2
                : (column++ * (NODE_W + GAP_X));
        }

        placed.set(node.id, { node, x, y: depth * (NODE_H + GAP_Y), depth });
        return x;
    };

    const seen = new Set();
    roots.forEach(root => walk(root, 0, seen));
    // Anything left is part of a cycle the API should have prevented; draw it anyway
    // rather than silently dropping devices off the map.
    nodes.forEach(node => {
        if (!placed.has(node.id)) {
            placed.set(node.id, { node, x: column++ * (NODE_W + GAP_X), y: 0, depth: 0 });
        }
    });

    return placed;
}

/* ------------------------------------------------------------------ render */

function renderTopology(topology) {
    const viewport = document.getElementById('topologyViewport');
    if (!viewport) return;

    const nodes = topology.nodes || [];
    if (nodes.length === 0) {
        renderInto('topology', viewport, `
            <div class="topology-empty">
                <strong>No devices yet</strong>
                Add a router or switch first — every other device connects through one.
            </div>`);
        return;
    }

    const placed = layoutTopology(nodes);
    const positions = [...placed.values()];
    const maxX = Math.max(...positions.map(p => p.x)) + NODE_W;
    const maxY = Math.max(...positions.map(p => p.y)) + NODE_H;
    const width = maxX + 8;
    const height = maxY + 8;

    const links = [];
    const boxes = [];

    positions.forEach(({ node, x, y }) => {
        const parent = node.parent_id !== null ? placed.get(node.parent_id) : null;
        if (parent) {
            const x1 = parent.x + NODE_W / 2;
            const y1 = parent.y + NODE_H;
            const x2 = x + NODE_W / 2;
            const y2 = y;
            const mid = y1 + (y2 - y1) / 2;
            // Orthogonal elbows read as cabling; a diagonal reads as an abstraction.
            const path = `M ${x1} ${y1} V ${mid} H ${x2} V ${y2}`;
            const cls = parent.node.status === 'offline'
                ? 'topo-link is-down'
                : node.derived_status === 'unreachable' ? 'topo-link is-blocked' : 'topo-link';
            links.push(`<path class="${cls}" d="${path}"/>`);
        }

        const state = node.derived_status;
        const label = state === 'unreachable' ? 'CUT OFF' : state.toUpperCase();
        const meta = node.child_count
            ? `${node.child_count} connected`
            : (typeof node.last_latency_ms === 'number' ? `${node.last_latency_ms.toFixed(1)} ms` : '');

        const faultRing = (node.status === 'offline' && state !== 'unreachable')
            ? `<circle class="topo-fault-ring" cx="26" cy="${NODE_H / 2}" r="17"
                       style="transform-origin: 26px ${NODE_H / 2}px"/>`
            : '';

        boxes.push(`
            <g class="topo-node is-${state}" transform="translate(${x}, ${y})"
               data-device-id="${node.id}" role="listitem"
               tabindex="0" aria-label="${escapeHtml(node.name)}, ${escapeHtml(node.type_label)}, ${escapeHtml(label)}">
                <title>${escapeHtml(node.name)} — ${escapeHtml(node.type_label)} — ${escapeHtml(node.ip_address)}</title>
                <rect class="node-box" width="${NODE_W}" height="${NODE_H}"/>
                ${faultRing}
                <g class="node-icon" transform="translate(14, ${NODE_H / 2 - 12})">${deviceIcon(node.device_type)}</g>
                <text class="node-name" x="46" y="24">${escapeHtml(truncate(node.name, 15))}</text>
                <text class="node-addr" x="46" y="39">${escapeHtml(node.ip_address)}</text>
                <text class="node-meta" x="46" y="53">${escapeHtml(label)}${meta ? ' · ' + escapeHtml(meta) : ''}</text>
            </g>`);
    });

    renderInto('topology', viewport, `
        <svg id="topologyCanvas" width="${width}" height="${height}"
             viewBox="0 0 ${width} ${height}" role="list"
             aria-label="Network topology, ${nodes.length} devices">
            <g>${links.join('')}</g>
            <g>${boxes.join('')}</g>
        </svg>`);
}

function truncate(text, max) {
    const value = String(text ?? '');
    return value.length > max ? value.slice(0, max - 1) + '…' : value;
}

/* Root cause, stated plainly: name the device that failed and how much is behind it. */
function renderIncidents(topology) {
    const bar = document.getElementById('incidentBar');
    if (!bar) return;

    const incidents = (topology.incidents || []).filter(i => i.devices_affected > 0);
    if (incidents.length === 0) {
        renderInto('incidents', bar, '');
        return;
    }

    renderInto('incidents', bar, incidents.map(incident => `
        <div class="incident-row">
            <span class="incident-icon">${deviceIcon('alert', 20)}</span>
            <span class="incident-text">
                <strong>${escapeHtml(incident.type_label)} ${escapeHtml(incident.device_name)}</strong>
                <span class="mono">${escapeHtml(incident.ip_address)}</span>
                is down — start here.
            </span>
            <span class="incident-impact">${incident.devices_affected} cut off</span>
        </div>
    `).join(''));
}
