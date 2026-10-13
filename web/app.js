"use strict";
(() => {
  const list = document.getElementById("posts");
  const posts = Array.from(list.children).map(element => ({
    element,
    title: element.dataset.title,
    date: element.dataset.date,
    search: element.dataset.search,
    ready: element.dataset.ready === "true",
    tags: JSON.parse(element.dataset.tags),
    categories: JSON.parse(element.dataset.categories),
  }));
  const search = document.getElementById("search");
  const ready = document.getElementById("ready");
  const sort = document.getElementById("sort");
  const title = document.getElementById("collection-title");
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
    const key = filtering ? `${kind}=${slug}` : "all";
    const term = terms.get(key) || slug;
    const query = search.value.normalize("NFKC").toLowerCase().trim();
    let count = 0;
    posts.sort((a, b) => sort.value === "title"
      ? a.title.localeCompare(b.title)
      : sort.value === "oldest" ? a.date.localeCompare(b.date) : b.date.localeCompare(a.date));
    const fragment = document.createDocumentFragment();
    for (const post of posts) {
      const matches = (!query || post.search.includes(query))
        && (!ready.checked || post.ready)
        && (!filtering || (kind === "tag" ? post.tags : post.categories).includes(slug));
      post.element.hidden = !matches;
      if (matches) count++;
      fragment.append(post.element);
    }
    list.append(fragment);
    title.textContent = filtering ? term : "All posts";
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
  sort.addEventListener("change", render);
  window.addEventListener("hashchange", render);
  render();
})();
