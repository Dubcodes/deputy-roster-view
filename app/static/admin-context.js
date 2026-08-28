(() => {
  "use strict";

  const STORAGE_KEY = "redeputy:admin-return-state:v1";
  const MAX_AGE_MS = 2 * 60 * 1000;

  const disclosureKey = (details, index) => {
    if (details.dataset.adminDisclosureKey) return details.dataset.adminDisclosureKey;
    const key = `auto-${index}`;
    details.dataset.adminDisclosureKey = key;
    return key;
  };

  const disclosures = () => [...document.querySelectorAll("details")];

  const eligibleForm = (form) => {
    if (!(form instanceof HTMLFormElement) || form.dataset.adminNoContext !== undefined) return false;
    if (String(form.method || "get").toLowerCase() !== "post") return false;
    if (window.location.pathname !== "/admin") return false;
    const action = new URL(form.getAttribute("action") || window.location.href, window.location.href);
    return action.origin === window.location.origin && (action.pathname === "/admin" || action.pathname.startsWith("/admin/"));
  };

  const save = (form) => {
    if (!eligibleForm(form)) return false;
    const now = Date.now();
    const open = disclosures()
      .map((details, index) => ({details, key: disclosureKey(details, index)}))
      .filter(({details}) => details.open)
      .map(({key}) => key);
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
        path: "/admin",
        open,
        scrollY: Math.max(0, Math.round(window.scrollY || 0)),
        savedAt: now,
        expiresAt: now + MAX_AGE_MS,
      }));
      return true;
    } catch (_error) {
      return false;
    }
  };

  const consume = () => {
    let raw = null;
    try {
      raw = window.sessionStorage.getItem(STORAGE_KEY);
      if (raw !== null) window.sessionStorage.removeItem(STORAGE_KEY);
    } catch (_error) {
      return null;
    }
    if (!raw || window.location.pathname !== "/admin") return null;
    try {
      const state = JSON.parse(raw);
      if (
        state?.path !== "/admin"
        || !Array.isArray(state.open)
        || !Number.isFinite(state.scrollY)
        || !Number.isFinite(state.savedAt)
        || !Number.isFinite(state.expiresAt)
        || state.expiresAt < Date.now()
        || state.savedAt > Date.now() + 5_000
      ) return null;
      return state;
    } catch (_error) {
      return null;
    }
  };

  const restore = () => {
    const state = consume();
    if (!state) return false;
    const openKeys = new Set(state.open);
    disclosures().forEach((details, index) => {
      if (openKeys.has(disclosureKey(details, index))) details.open = true;
    });
    // The page is already fully parsed here.  Restore immediately so a
    // redirected form return is stable before automation or a user sees it,
    // then repeat after layout in case an opened disclosure changes height.
    window.scrollTo(0, state.scrollY);
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => window.scrollTo(0, state.scrollY));
    });
    return true;
  };

  disclosures().forEach(disclosureKey);
  const restored = restore();
  if (!restored && window.location.hash === "#manual-work-days") {
    const manualWorkdays = document.querySelector('[data-admin-disclosure-key="manual-work-days"]');
    if (manualWorkdays) {
      manualWorkdays.open = true;
      window.requestAnimationFrame(() => manualWorkdays.scrollIntoView({block: "start"}));
    }
  }
  document.addEventListener("submit", (event) => {
    if (!event.defaultPrevented) save(event.target);
  });

  window.__redeputyAdminContext = Object.freeze({STORAGE_KEY, MAX_AGE_MS, eligibleForm, save, consume, restore});
})();
