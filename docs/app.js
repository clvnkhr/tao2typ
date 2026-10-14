"use strict";
(() => {
  const list = document.getElementById("posts");
  const posts = Array.from(list.children).map(element => ({
    element,
    title: element.dataset.title,
    date: element.dataset.date,
    search: element.dataset.search,
    ready: element.dataset.ready === "true",
    failed: element.dataset.failed === "true",
    id: element.dataset.id,
    queuePosition: element.dataset.queuePosition !== undefined && element.dataset.queuePosition !== ""
      && Number.isFinite(Number(element.dataset.queuePosition)) ? Number(element.dataset.queuePosition) : null,
    tags: JSON.parse(element.dataset.tags),
    categories: JSON.parse(element.dataset.categories),
  }));
  const search = document.getElementById("search");
  const ready = document.getElementById("ready");
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
  const localPreview = ["localhost", "127.0.0.1", "[::1]", "::1"].includes(window.location.hostname);
  const previewStateKey = `tao2typ:preview-controls:${window.location.pathname}`;
  if (localPreview) {
    try {
      const saved = JSON.parse(sessionStorage.getItem(previewStateKey));
      sessionStorage.removeItem(previewStateKey);
      if (saved && typeof saved.search === "string") search.value = saved.search;
      if (saved && typeof saved.ready === "boolean") ready.checked = saved.ready;
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
      : sort.value === "oldest" ? a.date.localeCompare(b.date) : b.date.localeCompare(a.date));
    const fragment = document.createDocumentFragment();
    for (const post of posts) {
      const matches = (!query || post.search.includes(query))
        && (!ready.checked || post.ready)
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
  ready.addEventListener("change", render);
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
            ready: ready.checked,
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
