const autoRefresh = document.querySelector("[data-auto-refresh]");
if (autoRefresh) {
  window.setTimeout(() => window.location.reload(), 3000);
}

const selectAll = document.getElementById("select-all");
if (selectAll) {
  selectAll.addEventListener("change", () => {
    document.querySelectorAll('input[name="selected"]').forEach((item) => {
      item.checked = selectAll.checked;
    });
  });
}
