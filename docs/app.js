"use strict";
(() => {
  const list = document.getElementById("posts");
  const posts = Array.from(list.children).map(element => ({
    element,
    title: element.dataset.title,
    date: element.dataset.date,
    updated: Date.parse(element.dataset.updated),
    search: element.dataset.search,
    failed: element.dataset.failed === "true",
    id: element.dataset.id,
    queuePosition: element.dataset.queuePosition !== undefined && element.dataset.queuePosition !== ""
      && Number.isFinite(Number(element.dataset.queuePosition)) ? Number(element.dataset.queuePosition) : null,
    tags: JSON.parse(element.dataset.tags),
    categories: JSON.parse(element.dataset.categories),
  }));
  const search = document.getElementById("search");
  const failed = document.getElementById("failed");
  const failureFilter = failed.closest("label");
  const sort = document.getElementById("sort");
  const title = document.getElementById("collection-title");
  const queueUpdated = document.getElementById("queue-updated");
  const updatedAt = new Date(queueUpdated.dataset.updated);
  const hasQueueTimestamp = Number.isFinite(updatedAt.getTime());
  if (hasQueueTimestamp) {
    const time = queueUpdated.querySelector("time");
    time.dateTime = updatedAt.toISOString();
    time.textContent = updatedAt.toISOString().slice(0, 16).replace("T", " ");
  }
  let selectedOrder = sort.value;
  const links = Array.from(document.querySelectorAll(".taxonomy a[data-route]"));
  const terms = new Map(links.map(link => [link.dataset.route, link.dataset.name]));
  function positionQualityTooltip(status) {
    const panel = status.querySelector(".quality-tooltip");
    const dot = status.getBoundingClientRect();
    const below = window.innerHeight - dot.bottom - 12;
    const above = dot.top - 12;
    const openAbove = below < 240 && above > below;
    panel.style.top = openAbove ? "auto" : "100%";
    panel.style.bottom = openAbove ? "100%" : "auto";
    panel.style.maxHeight = `${Math.max(60, Math.min(420, openAbove ? above : below))}px`;
    panel.style.left = "0px";
    const bounds = panel.getBoundingClientRect();
    if (bounds.right > window.innerWidth - 12) {
      panel.style.left = `${window.innerWidth - 12 - bounds.right}px`;
    }
  }
  for (const eventName of ["pointerover", "focusin"]) {
    list.addEventListener(eventName, event => {
      const status = event.target instanceof Element ? event.target.closest(".quality-status") : null;
      if (!status || event.relatedTarget instanceof Node && status.contains(event.relatedTarget)) return;
      status.classList.remove("tooltip-dismissed");
      positionQualityTooltip(status);
    });
  }
  list.addEventListener("keydown", event => {
    if (event.key !== "Escape" || !(event.target instanceof Element)) return;
    event.target.closest(".quality-status")?.classList.add("tooltip-dismissed");
  });
  const downloadMediaTypes = {
    pdf: "application/pdf",
    gzip: "application/gzip",
    typst: "text/plain; charset=utf-8",
    json: "application/json",
    text: "text/plain; charset=utf-8",
  };
  const downloadDialog = document.getElementById("download-dialog");
  const downloadForm = document.getElementById("download-form");
  const downloadPassword = document.getElementById("download-password");
  const downloadError = document.getElementById("download-password-error");
  const downloadStatus = document.getElementById("download-status");
  const downloadSubmit = document.getElementById("download-submit");
  const downloadCancel = document.getElementById("download-cancel");
  let downloadSelection = null;
  let downloadRequest = null;

  function clearDownloadState() {
    const button = downloadSelection?.button;
    downloadRequest?.abort();
    downloadRequest = null;
    downloadSelection = null;
    if (button) button.disabled = false;
    downloadForm.reset();
    downloadPassword.disabled = false;
    downloadPassword.removeAttribute("aria-invalid");
    downloadSubmit.disabled = false;
    downloadSubmit.removeAttribute("aria-busy");
    downloadError.textContent = "";
    downloadError.hidden = true;
    downloadStatus.textContent = "";
    downloadStatus.hidden = true;
    return button;
  }

  function closeDownloadDialog() {
    const button = clearDownloadState();
    if (downloadDialog.open) downloadDialog.close();
    if (button?.isConnected) button.focus({ preventScroll: true });
  }

  list.addEventListener("click", event => {
    const button = event.target instanceof Element ? event.target.closest("button.download") : null;
    if (!button || !list.contains(button) || button.disabled || downloadDialog.open) return;
    event.preventDefault();
    const url = button.dataset.downloadUrl;
    if (!url) return;
    clearDownloadState();
    downloadSelection = {
      button, url,
      filename: button.dataset.filename || "tao2typ.txt",
      format: button.dataset.downloadFormat || "text",
    };
    downloadDialog.showModal();
    downloadPassword.focus();
  });
  downloadCancel.addEventListener("click", closeDownloadDialog);
  downloadDialog.addEventListener("cancel", event => {
    event.preventDefault();
    closeDownloadDialog();
  });
  downloadDialog.addEventListener("close", () => {
    // A delayed close event must not reset a newly opened download prompt.
    if (!downloadDialog.open) {
      const button = clearDownloadState();
      if (button?.isConnected) button.focus({ preventScroll: true });
    }
  });
  downloadPassword.addEventListener("input", () => {
    downloadPassword.removeAttribute("aria-invalid");
    downloadError.textContent = "";
    downloadError.hidden = true;
  });
  downloadForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!downloadSelection || downloadRequest) return;
    if (downloadPassword.value !== "clvnkhr") {
      downloadPassword.value = "";
      downloadPassword.setAttribute("aria-invalid", "true");
      downloadError.textContent = "Incorrect password. Try again.";
      downloadError.hidden = false;
      downloadPassword.focus();
      return;
    }
    const selection = downloadSelection;
    const request = new AbortController();
    downloadRequest = request;
    downloadPassword.value = "";
    downloadPassword.removeAttribute("aria-invalid");
    downloadPassword.disabled = true;
    downloadError.textContent = "";
    downloadError.hidden = true;
    downloadSubmit.disabled = true;
    downloadSubmit.setAttribute("aria-busy", "true");
    selection.button.disabled = true;
    downloadStatus.textContent = "Downloading…";
    downloadStatus.hidden = false;
    try {
      const response = await fetch(selection.url, { signal: request.signal });
      if (downloadRequest !== request || !downloadDialog.open) return;
      if (!response.ok) throw new Error("Download failed");
      const blob = await response.blob();
      if (downloadRequest !== request || !downloadDialog.open) return;
      // Static hosts may use generic MIME types; check binary signatures when available.
      const expected = { pdf: [37, 80, 68, 70, 45], gzip: [31, 139, 8] }[selection.format];
      if (expected) {
        const signature = new Uint8Array(await blob.slice(0, expected.length).arrayBuffer());
        if (signature.length !== expected.length || !expected.every((byte, i) => signature[i] === byte)) {
          throw new Error("Download failed");
        }
      } else if (blob.type.split(";")[0] === "text/html") {
        throw new Error("Download failed");
      }
      if (downloadRequest !== request || request.signal.aborted || !downloadDialog.open) return;
      const objectUrl = URL.createObjectURL(new Blob([blob], {
        type: downloadMediaTypes[selection.format] || downloadMediaTypes.text,
      }));
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = selection.filename;
      document.body.append(link);
      try { link.click(); }
      finally {
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
      }
      closeDownloadDialog();
    } catch {
      if (downloadRequest !== request || request.signal.aborted) return;
      downloadError.textContent = "Download failed. Please try again.";
      downloadError.hidden = false;
    } finally {
      if (downloadRequest === request) {
        downloadRequest = null;
        selection.button.disabled = false;
        downloadPassword.disabled = false;
        downloadSubmit.disabled = false;
        downloadSubmit.removeAttribute("aria-busy");
        downloadStatus.textContent = "";
        downloadStatus.hidden = true;
        downloadPassword.focus();
      }
    }
  });
  const localPreview = ["localhost", "127.0.0.1", "[::1]", "::1"].includes(window.location.hostname);
  const previewStateKey = `tao2typ:preview-controls:${window.location.pathname}`;
  if (localPreview) {
    try {
      const saved = JSON.parse(sessionStorage.getItem(previewStateKey));
      sessionStorage.removeItem(previewStateKey);
      if (saved && typeof saved.search === "string") search.value = saved.search;
      if (saved && typeof saved.failed === "boolean") failed.checked = saved.failed;
      if (saved && Array.from(sort.options).some(option => option.value === saved.order && option.value !== "queue")) {
        selectedOrder = saved.order;
      }
      if (Array.isArray(saved?.openFailures)) {
        const openFailures = new Set(saved.openFailures);
        for (const post of posts) {
          const details = post.element.querySelector(".failure-details");
          if (details && openFailures.has(post.id)) details.open = true;
        }
      }
    } catch { /* Storage may be disabled in the browser. */ }
  }

  function render() {
    const route = window.location.hash.slice(1);
    const separator = route.indexOf("=");
    const kind = route.slice(0, separator);
    let slug;
    try { slug = decodeURIComponent(route.slice(separator + 1)); }
    catch { slug = ""; }
    const filtering = separator > 0 && ["tag", "category"].includes(kind);
    const isQueue = route === "queue";
    failureFilter.hidden = !isQueue;
    const key = filtering ? `${kind}=${slug}` : isQueue ? "queue" : "all";
    const term = terms.get(key) || slug;
    const query = search.value.normalize("NFKC").toLowerCase().trim();
    let count = 0;
    sort.disabled = isQueue;
    sort.querySelector('option[value="queue"]').hidden = !isQueue;
    sort.value = isQueue ? "queue" : selectedOrder;
    sort.setAttribute("aria-label", isQueue ? "Order: scheduled queue order" : "Order");
    list.classList.toggle("is-queue", isQueue);
    queueUpdated.hidden = !isQueue || !hasQueueTimestamp;
    posts.sort((a, b) => isQueue
      ? (a.queuePosition ?? Infinity) - (b.queuePosition ?? Infinity)
      : sort.value === "title"
      ? a.title.localeCompare(b.title)
      : sort.value === "updated"
      ? (Number.isFinite(b.updated) ? b.updated : -Infinity)
        - (Number.isFinite(a.updated) ? a.updated : -Infinity)
        || b.date.localeCompare(a.date) || a.id.localeCompare(b.id)
      : sort.value === "oldest" ? a.date.localeCompare(b.date) : b.date.localeCompare(a.date));
    const fragment = document.createDocumentFragment();
    for (const post of posts) {
      const matches = (!query || post.search.includes(query))
        && (!isQueue || !failed.checked || post.failed)
        && (!isQueue || post.queuePosition !== null)
        && (!filtering || (kind === "tag" ? post.tags : post.categories).includes(slug));
      post.element.hidden = !matches;
      if (matches) count++;
      fragment.append(post.element);
    }
    list.append(fragment);
    title.textContent = filtering ? term : isQueue ? "Queue" : "All posts";
    document.getElementById("result-count").textContent = `${count.toLocaleString()} ${count === 1 ? "post" : "posts"}`;
    document.getElementById("empty").hidden = count !== 0;
    for (const link of links) {
      if (link.dataset.route === key) {
        link.setAttribute("aria-current", "page");
        const details = link.closest("details");
        if (details) details.open = true;
      } else link.removeAttribute("aria-current");
    }
  }
  search.addEventListener("input", render);
  document.querySelector("form.controls").addEventListener("submit", event => event.preventDefault());
  failed.addEventListener("change", render);
  sort.addEventListener("change", () => {
    if (!sort.disabled) selectedOrder = sort.value;
    render();
  });
  window.addEventListener("hashchange", render);
  render();
  if (localPreview) {
    const batchProgress = document.createElement("p");
    batchProgress.id = "batch-progress";
    batchProgress.className = "queue-updated";
    batchProgress.setAttribute("aria-live", "polite");
    batchProgress.hidden = true;
    queueUpdated.after(batchProgress);
    let revision = document.body.dataset.previewRevision || null;
    let polling = false;
    async function refreshPreview() {
      if (polling) return;
      polling = true;
      try {
        const response = await fetch("/__tao2typ_progress", { cache: "no-store" });
        if (!response.ok) return;
        const progress = await response.json();
        if (typeof progress?.revision !== "string") return;
        if (["converted", "failed", "remaining"].every(key => Number.isInteger(progress[key]) && progress[key] >= 0)) {
          const status = `${progress.running === false ? "Finished" : "Batch"}: ${progress.converted.toLocaleString()} converted · ${progress.failed.toLocaleString()} failed · ${progress.remaining.toLocaleString()} remaining`;
          if (batchProgress.textContent !== status) batchProgress.textContent = status;
          batchProgress.hidden = false;
        }
        if (revision === null) {
          revision = progress.revision;
          return;
        }
        if (revision === progress.revision) return;
        try {
          sessionStorage.setItem(previewStateKey, JSON.stringify({
            search: search.value,
            failed: failed.checked,
            order: selectedOrder,
            openFailures: posts.filter(post => post.element.querySelector(".failure-details")?.open).map(post => post.id),
          }));
        } catch { /* Refresh still works when storage is unavailable. */ }
        window.location.reload();
      } catch { /* A stopped preview server should not interrupt browsing. */ }
      finally { polling = false; }
    }
    void refreshPreview();
    window.setInterval(refreshPreview, 15000);
  }
})();
