#!/usr/bin/env python3
"""Render the cloud worker investigation docs as HTML pages with a Copy button on every code block.

Usage: python3 cloud_worker_docs_html.py   (writes a .html next to each .md)
"""
import html
import re
from pathlib import Path

from markdown_it import MarkdownIt

DOCS = ["CLOUD_WORKER_INVESTIGATION_STEPS.md", "CLOUD_WORKER_DOCKER_REPORT.md"]
HERE = Path(__file__).resolve().parent

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font: 15px/1.55 -apple-system, "Segoe UI", Ubuntu, sans-serif; color: #1f2328; margin: 0; }}
  main {{ max-width: 1000px; margin: 0 auto; padding: 24px 32px 80px; }}
  h1, h2, h3, h4 {{ line-height: 1.25; margin: 1.6em 0 0.6em; }}
  h1 {{ font-size: 2em; border-bottom: 1px solid #d1d9e0; padding-bottom: .3em; }}
  h2 {{ font-size: 1.5em; border-bottom: 1px solid #d1d9e0; padding-bottom: .3em; }}
  h3 {{ font-size: 1.2em; }}
  a {{ color: #0969da; }}
  code {{ font: 13px/1.45 ui-monospace, "DejaVu Sans Mono", monospace; background: #eff1f3; padding: .15em .35em; border-radius: 4px; }}
  .code {{ position: relative; margin: 12px 0 16px; }}
  .code pre {{ background: #f6f8fa; border: 1px solid #d1d9e0; border-radius: 6px; padding: 14px 16px; padding-right: 90px; overflow-x: auto; margin: 0; }}
  .code pre code {{ background: none; padding: 0; white-space: pre; }}
  .copy {{ position: absolute; top: 8px; right: 8px; font: 13px sans-serif; padding: 4px 12px; border: 1px solid #d1d9e0;
           border-radius: 6px; background: #fff; cursor: pointer; }}
  .copy:hover {{ background: #f3f4f6; }}
  .copy.done {{ background: #dafbe1; border-color: #4ac26b; }}
  table {{ border-collapse: collapse; margin: 12px 0 16px; display: block; overflow-x: auto; }}
  th, td {{ border: 1px solid #d1d9e0; padding: 6px 12px; vertical-align: top; }}
  th {{ background: #f6f8fa; }}
  nav {{ background: #f6f8fa; border: 1px solid #d1d9e0; border-radius: 6px; padding: 8px 20px; margin: 16px 0; }}
  nav ul {{ margin: 4px 0; padding-left: 20px; }}
  nav li.h3 {{ margin-left: 20px; font-size: 14px; }}
  .note {{ color: #59636e; font-size: 13px; }}
</style>
</head>
<body>
<main>
<p class="note">Generated from <code>{source}</code>. Edit the .md, then run <code>python3 cloud_worker_docs_html.py</code>.</p>
{body}
</main>
<script>
document.querySelectorAll('.copy').forEach(function (button) {{
  button.addEventListener('click', function () {{
    var text = button.parentElement.querySelector('code').textContent;
    function done() {{
      button.textContent = 'Copied';
      button.classList.add('done');
      setTimeout(function () {{ button.textContent = 'Copy'; button.classList.remove('done'); }}, 1500);
    }}
    function fallback() {{
      var area = document.createElement('textarea');
      area.value = text;
      document.body.appendChild(area);
      area.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(area);
      if (ok) {{ done(); }} else {{ button.textContent = 'Copy failed: select the text'; }}
    }}
    if (navigator.clipboard && window.isSecureContext) {{
      navigator.clipboard.writeText(text).then(done, fallback);
    }} else {{
      fallback();
    }}
  }});
}});
</script>
</body>
</html>
"""


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render(md_path):
    md = MarkdownIt("commonmark").enable("table")
    md.add_render_rule("fence", lambda self, tokens, idx, options, env: (
        '<div class="code"><button class="copy" type="button">Copy</button>'
        f'<pre><code>{html.escape(tokens[idx].content)}</code></pre></div>\n'))
    tokens = md.parse(md_path.read_text())
    toc, seen = [], set()
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.tag in ("h2", "h3"):
            title = tokens[i + 1].content
            anchor = slug(title)
            while anchor in seen:
                anchor += "-x"
            seen.add(anchor)
            tok.attrSet("id", anchor)
            label = html.escape(re.sub(r"`", "", title))
            toc.append(f'<li class="{tok.tag}"><a href="#{anchor}">{label}</a></li>')
        if tok.type == "inline":
            for child in tok.children or []:
                href = child.attrGet("href") if child.type == "link_open" else None
                if href and href.endswith(".md") and href in DOCS:
                    child.attrSet("href", href[:-3] + ".html")
    body = md.renderer.render(tokens, md.options, {})
    body = body.replace("</h1>", f"</h1>\n<nav><ul>{''.join(toc)}</ul></nav>", 1)
    title = next((tokens[i + 1].content for i, t in enumerate(tokens) if t.tag == "h1"), md_path.stem)
    out = md_path.with_suffix(".html")
    out.write_text(PAGE.format(title=html.escape(title), source=md_path.name, body=body))
    return out


if __name__ == "__main__":
    for name in DOCS:
        print(render(HERE / name))
