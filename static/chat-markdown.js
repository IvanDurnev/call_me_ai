(() => {
  function render(node, value) {
    const text = String(value ?? "");
    if (!window.marked || !window.DOMPurify) {
      node.textContent = text;
      return;
    }
    try {
      const html = window.marked.parse(text, {gfm: true, breaks: true, async: false});
      const fragment = window.DOMPurify.sanitize(html, {
        RETURN_DOM_FRAGMENT: true,
        ALLOWED_TAGS: ["p", "br", "strong", "em", "del", "a", "ul", "ol", "li", "blockquote", "pre", "code", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "table", "thead", "tbody", "tr", "th", "td"],
        ALLOWED_ATTR: ["href", "title", "start", "align"],
      });
      for (const link of fragment.querySelectorAll("a[href]")) {
        const url = new URL(link.getAttribute("href"), window.location.href);
        if (!["http:", "https:", "mailto:"].includes(url.protocol)) {
          link.removeAttribute("href");
          continue;
        }
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      }
      node.classList.add("markdown-body");
      node.replaceChildren(fragment);
    } catch {
      node.classList.remove("markdown-body");
      node.textContent = text;
    }
  }
  window.ChatMarkdown = {render};
})();
