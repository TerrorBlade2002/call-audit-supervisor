// Tiny, dependency-free markdown → HTML for the prompt preview. Escapes first (admin-authored
// content, but never trust input), then handles headings, bold/italic/code, and bullet lists.
function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(s: string): string {
  return escapeHtml(s)
    .replace(/`([^`]+)`/g, '<code class="rounded bg-black/10 px-1 text-[0.85em]">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

export function renderMarkdown(md: string): string {
  const out: string[] = [];
  let inList = false;
  const closeList = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };
  for (const raw of md.split("\n")) {
    const line = raw.trimEnd();
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    const li = line.match(/^\s*[-*]\s+(.*)$/);
    if (h) {
      closeList();
      const lvl = h[1].length;
      out.push(`<h${lvl} class="mt-3 mb-1 font-semibold">${inline(h[2])}</h${lvl}>`);
    } else if (li) {
      if (!inList) {
        out.push('<ul class="my-1 list-disc space-y-0.5 pl-5">');
        inList = true;
      }
      out.push(`<li>${inline(li[1])}</li>`);
    } else if (!line.trim()) {
      closeList();
    } else {
      closeList();
      out.push(`<p class="my-1">${inline(line)}</p>`);
    }
  }
  closeList();
  return out.join("\n");
}
