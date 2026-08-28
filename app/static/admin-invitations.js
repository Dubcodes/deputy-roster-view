(() => {
  "use strict";

  const STORAGE_KEY = "redeputy:admin-invite-links:v1";
  const HANDOFF_KEY = "redeputy-invite";

  const readLinks = () => {
    try {
      const parsed = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || "{}");
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch (_error) {
      return {};
    }
  };

  const writeLinks = (links) => {
    try {
      if (Object.keys(links).length) window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(links));
      else window.sessionStorage.removeItem(STORAGE_KEY);
    } catch (_error) {
      // The Admin page remains fully usable without the client-only link cache.
    }
  };

  const decodeHandoff = (encoded) => {
    try {
      const padded = encoded.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(encoded.length / 4) * 4, "=");
      const payload = JSON.parse(window.atob(padded));
      if (!['account', 'contractor'].includes(payload?.kind)) return null;
      if (!Number.isInteger(payload.id) || payload.id < 1) return null;
      if (!/^[A-Za-z0-9_-]{40,}$/.test(payload.token || "")) return null;
      if (!Number.isFinite(Date.parse(payload.expiresAt))) return null;
      return payload;
    } catch (_error) {
      return null;
    }
  };

  const consumeHandoff = () => {
    if (!window.location.hash.startsWith(`#${HANDOFF_KEY}=`)) return null;
    const encoded = window.location.hash.slice(HANDOFF_KEY.length + 2);
    const payload = decodeHandoff(encoded);
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}`);
    if (!payload) return null;
    const links = readLinks();
    links[`${payload.kind}:${payload.id}`] = {
      token: payload.token,
      expiresAt: payload.expiresAt,
    };
    writeLinks(links);
    return payload;
  };

  const activationUrl = (kind, token) => `${window.location.origin}/${kind === 'account' ? 'account/invite' : 'contractor/invite'}/${token}`;

  const countdownText = (expiresAt) => {
    const remainingMinutes = Math.max(0, Math.ceil((Date.parse(expiresAt) - Date.now()) / 60_000));
    if (!remainingMinutes) return "Expired";
    const hours = Math.floor(remainingMinutes / 60);
    const minutes = remainingMinutes % 60;
    return `Expires in ${hours ? `${hours}h ` : ""}${minutes}m`;
  };

  const render = () => {
    const links = readLinks();
    const presentKeys = new Set();
    document.querySelectorAll("[data-invite-row]").forEach((row) => {
      const key = row.dataset.inviteKey;
      presentKeys.add(key);
      const entry = links[key];
      const available = row.dataset.inviteAvailable === "true" && Date.parse(row.dataset.inviteExpiresAt || "") > Date.now();
      if (!available || !entry || Date.parse(entry.expiresAt) <= Date.now()) {
        if (entry) delete links[key];
      } else {
        const input = row.querySelector("[data-invite-link-input]");
        const link = row.querySelector("[data-invite-link]");
        if (input) input.value = activationUrl(row.dataset.inviteKind, entry.token);
        if (link) link.hidden = false;
        const missing = row.querySelector("[data-invite-link-missing]");
        if (missing) missing.hidden = true;
      }
      const countdown = row.querySelector("[data-invite-countdown]");
      if (countdown && available) countdown.textContent = countdownText(row.dataset.inviteExpiresAt);
    });
    Object.keys(links).forEach((key) => {
      if (!presentKeys.has(key)) delete links[key];
    });
    writeLinks(links);
  };

  const handoff = consumeHandoff();
  if (handoff) {
    document.querySelector('[data-admin-disclosure-key="accounts"]')?.setAttribute("open", "");
    document.querySelector(`[data-admin-disclosure-key="${handoff.kind === 'account' ? 'account-invitations' : 'contractors'}"]`)?.setAttribute("open", "");
  }
  render();
  window.setInterval(render, 60_000);

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-invite-copy]");
    if (!button) return;
    const input = button.closest("[data-invite-row]")?.querySelector("[data-invite-link-input]");
    if (!input?.value) return;
    try {
      await navigator.clipboard.writeText(input.value);
      button.textContent = "Copied";
    } catch (_error) {
      input.focus();
      input.select();
      document.execCommand?.("copy");
      button.textContent = "Selected";
    }
  });

  window.__redeputyAdminInvitations = Object.freeze({STORAGE_KEY, decodeHandoff, consumeHandoff, countdownText, render});
})();
