/* ============================================================================
   Guarded rendering.

   The dashboard polls every five seconds so the data on screen is live. The
   original code responded to each poll by rewriting whole sections with
   innerHTML, which replaces every node inside them. That discards whatever the
   person was doing in that section: the scroll position, the open dropdown, the
   selected text, the field being typed into. It reads as the page reloading
   itself, and it made forms unusable while data was arriving.

   Every render in this application goes through here, so that cost is paid only
   when the markup has genuinely changed, and never while someone is working
   inside the region being replaced.
   ========================================================================== */

const RenderGuard = (() => {
    const lastMarkup = new Map();

    /* True when the keyboard focus sits inside this region — the person is typing
       in it, or has a dropdown open. Replacing it now would interrupt them. */
    function isBusy(element) {
        const active = document.activeElement;
        if (!active || active === document.body) return false;
        return element === active || element.contains(active);
    }

    function nearestScroller(element) {
        let node = element;
        while (node && node !== document.body) {
            const style = getComputedStyle(node);
            if (/(auto|scroll)/.test(style.overflowY + style.overflowX)) return node;
            node = node.parentElement;
        }
        return null;
    }

    /* Write html into element, but only if it differs from what is already there
       and the region is idle. Returns whether the DOM was touched. */
    function renderInto(key, element, html) {
        if (!element) return false;
        if (lastMarkup.get(key) === html) return false;
        if (isBusy(element)) return false;

        const scroller = nearestScroller(element);
        const top = scroller ? scroller.scrollTop : 0;
        const left = scroller ? scroller.scrollLeft : 0;

        element.innerHTML = html;
        lastMarkup.set(key, html);

        if (scroller) {
            scroller.scrollTop = top;
            scroller.scrollLeft = left;
        }
        return true;
    }

    /* Rebuilding a <select> clears its value and shuts an open dropdown, so the
       current choice is carried across and a focused select is left alone. */
    function renderOptions(key, select, options, { placeholder = null } = {}) {
        if (!select) return false;

        const items = placeholder ? [placeholder, ...options] : options;
        const html = items
            .map(o => `<option value="${escapeAttribute(o.value)}">${escapeText(o.label)}</option>`)
            .join('');

        if (lastMarkup.get(key) === html) return false;
        if (isBusy(select)) return false;

        const previous = select.value;
        select.innerHTML = html;
        lastMarkup.set(key, html);

        if (previous && items.some(o => String(o.value) === previous)) {
            select.value = previous;
        }
        return true;
    }

    /* Drop the cached markup so the next render writes unconditionally. Used when
       something outside the data changes the output — a theme switch, or a dialog
       opening that needs its options refreshed immediately. */
    function invalidate(key) {
        if (key === undefined) lastMarkup.clear();
        else lastMarkup.delete(key);
    }

    function escapeText(value) {
        if (value === null || value === undefined) return '';
        const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
        return String(value).replace(/[&<>"']/g, m => map[m]);
    }

    const escapeAttribute = escapeText;

    return { renderInto, renderOptions, invalidate, isBusy };
})();

const renderInto = RenderGuard.renderInto;
const renderOptions = RenderGuard.renderOptions;
const invalidateRender = RenderGuard.invalidate;
