(() => {
  const notice = document.querySelector("[data-one-shot-notice]");
  if (!notice) return;

  const url = new URL(window.location.href);
  if (!url.searchParams.has("notice")) return;

  url.searchParams.delete("notice");
  window.history.replaceState(
    window.history.state,
    "",
    `${url.pathname}${url.search}${url.hash}`,
  );
})();
