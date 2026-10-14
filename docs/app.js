"use strict";
(() => {
  const list = document.getElementById("posts");
  const posts = Array.from(list.children).map(element => ({
    element,
    title: element.dataset.title,
    date: element.dataset.date,
    search: element.dataset.search,
    ready: element.dataset.ready === "true",
    queuePosition: element.dataset.queuePosition !== undefined && element.dataset.queuePosition !== ""
      && Number.isFinite(Number(element.dataset.queuePosition)) ? Number(element.dataset.queuePosition) : null,
    tags: JSON.parse(element.dataset.tags),
    categories: JSON.parse(element.dataset.categories),
  }));
  const search = document.getElementById("search");
  const ready = document.getElementById("ready");
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

  function render() {
    const route = window.location.hash.slice(1);
    const separator = route.indexOf("=");
    const kind = route.slice(0, separator);
    let slug;
    try { slug = decodeURIComponent(route.slice(separator + 1)); }
    catch { slug = ""; }
    const filtering = separator > 0 && ["tag", "category"].includes(kind);
    const isQueue = route === "queue";
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
  sort.addEventListener("change", () => {
    if (!sort.disabled) selectedOrder = sort.value;
    render();
  });
  window.addEventListener("hashchange", render);
  render();
})();
