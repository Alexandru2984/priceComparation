document.querySelectorAll("[data-product-autocomplete]").forEach((root) => {
  const hidden = root.querySelector('input[type="hidden"]');
  const search = root.querySelector('input[type="search"]');
  const results = root.querySelector('[role="listbox"]');
  let timer = null;
  let controller = null;

  const close = () => {
    results.hidden = true;
    search.setAttribute("aria-expanded", "false");
  };

  const show = (products) => {
    results.replaceChildren();
    if (!products.length) {
      const empty = document.createElement("span");
      empty.className = "autocomplete-empty";
      empty.textContent = "Niciun produs găsit";
      results.appendChild(empty);
    }
    products.forEach((product) => {
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("role", "option");
      button.textContent = product.label;
      button.addEventListener("click", () => {
        hidden.value = product.id;
        search.value = product.label;
        search.dataset.selectedLabel = product.label;
        close();
      });
      results.appendChild(button);
    });
    results.hidden = false;
    search.setAttribute("aria-expanded", "true");
  };

  const fetchProducts = async () => {
    const query = search.value.trim();
    if (query.length < 2) {
      close();
      return;
    }
    if (controller) controller.abort();
    controller = new AbortController();
    try {
      const response = await fetch(`${root.dataset.searchUrl}?q=${encodeURIComponent(query)}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("search failed");
      const data = await response.json();
      show(data.products);
    } catch (error) {
      if (error.name !== "AbortError") close();
    }
  };

  search.dataset.selectedLabel = search.value;
  search.addEventListener("input", () => {
    if (search.value !== search.dataset.selectedLabel) hidden.value = "";
    window.clearTimeout(timer);
    timer = window.setTimeout(fetchProducts, 220);
  });
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });
  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) close();
  });
});
