/* Theme selection.
   Loaded in <head> so the stored choice is applied before first paint — setting it
   from the main bundle would show a flash of the wrong theme on every page load.
   Three states: 'light', 'dark', or 'system' (no attribute, follow the OS). */
(function () {
    const KEY = 'netmon-theme';

    function stored() {
        try { return localStorage.getItem(KEY); } catch { return null; }
    }

    function apply(choice) {
        const root = document.documentElement;
        if (choice === 'light' || choice === 'dark') {
            root.setAttribute('data-theme', choice);
        } else {
            root.removeAttribute('data-theme');
        }
    }

    apply(stored());

    window.NetmonTheme = {
        get current() { return stored() || 'system'; },
        set(choice) {
            try {
                if (choice === 'system') localStorage.removeItem(KEY);
                else localStorage.setItem(KEY, choice);
            } catch { /* private browsing — the choice just won't persist */ }
            apply(choice === 'system' ? null : choice);
            document.dispatchEvent(new CustomEvent('themechange', { detail: choice }));
        }
    };
})();
