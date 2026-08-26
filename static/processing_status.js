(() => {
  if (!document.querySelector("[data-processing-active='1']")) return;
  window.setTimeout(() => window.location.reload(), 3000);
})();
