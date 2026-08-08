document.querySelectorAll("[data-width]").forEach((bar) => {
  bar.style.width = `${bar.dataset.width}%`;
});
