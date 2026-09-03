document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const formatUploadSize = (bytes) => {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

document.querySelectorAll("[data-document-upload]").forEach((uploadArea) => {
  const cameraContainer = uploadArea.querySelector("[data-camera-inputs]");
  const cameraTrigger = uploadArea.querySelector("[data-camera-trigger]");
  const galleryInput = uploadArea.querySelector("[data-gallery-input]");
  const summary = uploadArea.querySelector("[data-upload-summary]");
  const clearButton = uploadArea.querySelector("[data-upload-clear]");
  const form = uploadArea.closest("form");
  const maxFiles = Number(uploadArea.dataset.maxFiles);
  const maxFileBytes = Number(uploadArea.dataset.maxFileBytes);
  const maxTotalBytes = Number(uploadArea.dataset.maxTotalBytes);
  let cameraIndex = 0;

  const fileInputs = () => Array.from(uploadArea.querySelectorAll('input[type="file"]'));
  const selectedFiles = () => fileInputs().flatMap((input) => Array.from(input.files || []));

  const validationMessage = (files) => {
    if (uploadArea.dataset.uploadRequired === "true" && files.length === 0) {
      return "Fotografiază documentul sau alege cel puțin un fișier.";
    }
    if (files.length > maxFiles) return `Ai selectat ${files.length} fișiere; limita este ${maxFiles}.`;
    const oversized = files.find((file) => file.size > maxFileBytes);
    if (oversized) return `„${oversized.name}” depășește limita de 10 MB.`;
    const totalSize = files.reduce((total, file) => total + file.size, 0);
    if (totalSize > maxTotalBytes) return "Selecția depășește limita totală de 50 MB.";
    return "";
  };

  const refreshSummary = (forceValidation = false) => {
    const files = selectedFiles();
    const issue = validationMessage(files);
    const shouldShowIssue = issue && (files.length > 0 || forceValidation);
    summary.classList.toggle("error", Boolean(shouldShowIssue));
    if (shouldShowIssue) {
      summary.textContent = issue;
    } else if (files.length) {
      const totalSize = files.reduce((total, file) => total + file.size, 0);
      const names = files.map((file) => file.name).join(", ");
      summary.textContent = `${files.length} ${files.length === 1 ? "fișier selectat" : "fișiere selectate"} · ${formatUploadSize(totalSize)} · ${names}`;
    } else {
      summary.textContent = "Niciun fișier selectat.";
    }
    clearButton.hidden = files.length === 0;
    return shouldShowIssue ? issue : "";
  };

  const bindCameraInput = (input) => {
    input.addEventListener("change", () => {
      if (input.files && input.files.length) {
        const nextInput = input.cloneNode(false);
        cameraIndex += 1;
        nextInput.id = `${input.id.split("--camera-")[0]}--camera-${cameraIndex}`;
        nextInput.value = "";
        cameraContainer.appendChild(nextInput);
        bindCameraInput(nextInput);
        cameraTrigger.htmlFor = nextInput.id;
      }
      refreshSummary();
    });
  };

  cameraContainer.querySelectorAll("[data-camera-input]").forEach(bindCameraInput);
  galleryInput.addEventListener("change", () => refreshSummary());

  clearButton.addEventListener("click", () => {
    const cameraInputs = Array.from(cameraContainer.querySelectorAll("[data-camera-input]"));
    const currentCameraInput = cameraInputs[cameraInputs.length - 1];
    cameraInputs.slice(0, -1).forEach((input) => input.remove());
    currentCameraInput.value = "";
    galleryInput.value = "";
    cameraTrigger.htmlFor = currentCameraInput.id;
    refreshSummary();
  });

  form.addEventListener("submit", (event) => {
    if (refreshSummary(true)) {
      event.preventDefault();
      summary.setAttribute("tabindex", "-1");
      summary.focus();
    }
  });
});
