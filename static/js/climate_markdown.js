/**
 * CLIMATE Markdown renderer — marked (GFM) + DOMPurify + highlight.js.
 * Shared by AI chat and Markdown file Preview. Raw source is never mutated here.
 */
(function (root) {
  "use strict";

  var ALLOWED_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "em", "strong", "i", "b", "del", "s", "u",
    "ul", "ol", "li",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "pre", "code", "blockquote",
    "a", "span", "div",
    "details", "summary",
    "input",
    "sup", "sub"
  ];
  var ALLOWED_ATTR = [
    "href", "title", "target", "rel", "class", "id", "open",
    "disabled", "checked", "type", "start", "align", "colspan", "rowspan"
  ];

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function configureMarked() {
    var marked = root.marked;
    if (!marked) return;
    if (typeof marked.use === "function") {
      marked.use({ gfm: true, breaks: false, pedantic: false });
    } else if (typeof marked.setOptions === "function") {
      marked.setOptions({ gfm: true, breaks: false, headerIds: false, mangle: false });
    }
  }

  function parseMarkdown(text) {
    var marked = root.marked;
    if (!marked) return "<pre>" + escapeHtml(text) + "</pre>";
    try {
      if (typeof marked.parse === "function") return marked.parse(text);
      if (typeof marked === "function") return marked(text);
    } catch (_) {
      return "<pre>" + escapeHtml(text) + "</pre>";
    }
    return "<pre>" + escapeHtml(text) + "</pre>";
  }

  function sanitizeHtml(html) {
    var purify = root.DOMPurify;
    if (!purify || typeof purify.sanitize !== "function") {
      return escapeHtml(html);
    }
    return purify.sanitize(html, {
      ALLOWED_TAGS: ALLOWED_TAGS,
      ALLOWED_ATTR: ALLOWED_ATTR,
      ALLOW_DATA_ATTR: false,
      FORBID_TAGS: ["script", "style", "iframe", "object", "embed", "form", "button", "svg", "math"],
      FORBID_ATTR: ["style", "srcdoc"],
      ADD_ATTR: ["target", "rel", "open", "disabled", "checked", "class"],
      ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i
    });
  }

  function rewriteLinks(html) {
    return String(html || "").replace(/<a\b([^>]*?)>/gi, function (tag, attrs) {
      var href = /href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i.exec(attrs || "");
      var url = href ? (href[1] || href[2] || href[3] || "") : "";
      if (!url || /^(https?:|mailto:)/i.test(url)) {
        if (/target=/i.test(attrs || "")) {
          if (!/\brel=/i.test(attrs || "")) attrs += ' rel="noopener noreferrer"';
          return "<a" + attrs + ">";
        }
        if (/^(https?:)/i.test(url)) {
          return "<a" + attrs + ' target="_blank" rel="noopener noreferrer">';
        }
      }
      return "<a" + attrs + ">";
    });
  }

  function wrapTables(html) {
    return String(html || "").replace(/<table\b[\s\S]*?<\/table>/gi, function (table) {
      if (/climate-md-table-wrap/.test(table)) return table;
      return '<div class="climate-md-table-wrap">' + table + "</div>";
    });
  }

  function wrapCodeBlocks(html) {
    return String(html || "").replace(/<pre\b[\s\S]*?<\/pre>/gi, function (block) {
      if (/climate-md-code/.test(block)) return block;
      return '<div class="climate-md-code"><button type="button" class="climate-md-copy" data-md-copy>Copy</button>' + block + "</div>";
    });
  }

  function highlightHtml(html) {
    var hljs = root.hljs;
    if (!hljs || typeof hljs.highlight !== "function") return html;
    return String(html || "").replace(
      /<code\b([^>]*)class="([^"]*language-([A-Za-z0-9_+-]+)[^"]*)"([^>]*)>([\s\S]*?)<\/code>/gi,
      function (full, pre, cls, lang, post, body) {
        if (/\bhljs\b/.test(cls)) return full;
        var raw = body.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/&quot;/g, '"');
        try {
          var result = hljs.highlight(raw, { language: lang, ignoreIllegals: true });
          if (result && result.value) {
            return '<code class="' + cls + ' hljs"' + post + ">" + result.value + "</code>";
          }
        } catch (_) {}
        return full;
      }
    );
  }

  function decodeEntities(value) {
    return String(value || "")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&amp;/g, "&");
  }

  function stripTags(html) {
    return String(html || "").replace(/<[^>]+>/g, "");
  }

  var FILE_EXT = "py|pyi|js|mjs|cjs|ts|tsx|jsx|json|ya?ml|md|markdown|html?|css|scss|sql|toml|ini|cfg|conf|xml|sh|bash|bat|ps1|go|rs|java|kt|c|h|cpp|hpp|cs|php|vue|r|rb|txt|csv";
  var FILE_PATH_RE = new RegExp(
    "^(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+\\.(?:" + FILE_EXT + ")$",
    "i"
  );
  var FILE_IN_TEXT_RE = new RegExp(
    "(?:^|[^A-Za-z0-9_./\\\\])((?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+\\.(?:" + FILE_EXT + "))" +
      "(?:(?::(\\d+))|(?:\\s*(?:[—–−]|--|[-:])\\s+([A-Za-z_][A-Za-z0-9_]*)))?",
    "g"
  );

  function isDhis2Uid(value) {
    var text = String(value || "").trim();
    if (!/^[A-Za-z][A-Za-z0-9]{10}$/.test(text)) return false;
    return /[0-9]/.test(text);
  }

  function isSymbolName(value) {
    var text = String(value || "").trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(text)) return false;
    if (text.length < 2 || text.length > 80) return false;
    return !isDhis2Uid(text);
  }

  function isRepoFilePath(value) {
    var text = String(value || "").trim().replace(/\\/g, "/");
    if (!text || /:\/\//.test(text) || text.charAt(0) === "/" || text.charAt(0) === ".") return false;
    return FILE_PATH_RE.test(text);
  }

  function parseFileRef(value) {
    var text = decodeEntities(String(value || "")).trim().replace(/\\/g, "/");
    if (!text) return null;
    var pair = text.match(/^(.+?)\s*(?:[—–−]|--|[-:])\s+([A-Za-z_][A-Za-z0-9_]*)$/);
    var path = text;
    var symbol = "";
    var line = 0;
    if (pair && isRepoFilePath(pair[1])) {
      path = pair[1].trim();
      symbol = pair[2];
    }
    var lined = path.match(/^(.+):(\d+)$/);
    if (lined && isRepoFilePath(lined[1])) {
      path = lined[1];
      line = parseInt(lined[2], 10) || 0;
    }
    var coded = path.match(/^(.+):([A-Za-z_][A-Za-z0-9_]*)$/);
    if (coded && isRepoFilePath(coded[1]) && isSymbolName(coded[2])) {
      path = coded[1];
      symbol = coded[2];
    }
    if (!isRepoFilePath(path)) return null;
    if (symbol && !isSymbolName(symbol)) symbol = "";
    return { path: path, symbol: symbol || "", line: line || 0 };
  }

  function uidChip(uid) {
    return '<button type="button" class="climate-uid" data-uid-copy title="Copy UID" aria-label="Copy UID">' +
      escapeHtml(uid) + "</button>";
  }

  function fileRefChip(path, symbol, line) {
    var ref = parseFileRef(path) || { path: path, symbol: "", line: 0 };
    path = ref.path;
    symbol = symbol || ref.symbol || "";
    line = Number(line || ref.line || 0) || 0;
    var label = escapeHtml(path);
    if (symbol) label += '<span class="climate-file-ref-sep"> — </span>' + escapeHtml(symbol);
    else if (line) label += '<span class="climate-file-ref-sep">:</span>' + escapeHtml(String(line));
    var title = "Open " + path + (symbol ? " · " + symbol : (line ? ":" + line : ""));
    return '<button type="button" class="climate-file-ref" data-open-file="' + escapeHtml(path) + '"' +
      (symbol ? ' data-open-symbol="' + escapeHtml(symbol) + '"' : "") +
      (line ? ' data-open-line="' + escapeHtml(String(line)) + '"' : "") +
      ' title="' + escapeHtml(title) + '">' + label + "</button>";
  }

  function matchTrailingSymbol(html, from) {
    var slice = String(html || "").slice(from);
    var match = /^(\s*(?:—|–|−|&mdash;|&ndash;|&minus;|&amp;mdash;|--|[-:])\s*)(?:<code\b[^>]*>)([^<]+)(?:<\/code>)/i.exec(slice);
    if (!match) return null;
    var symbol = decodeEntities(stripTags(match[2])).trim();
    if (!isSymbolName(symbol)) return null;
    return { symbol: symbol, end: from + match[0].length };
  }

  function decorateFilePlainText(text) {
    FILE_IN_TEXT_RE.lastIndex = 0;
    return String(text || "").replace(FILE_IN_TEXT_RE, function (full, path, line, symbol, offset) {
      var prefix = full.slice(0, full.indexOf(path));
      var ref = parseFileRef(path);
      if (!ref) return full;
      return prefix + fileRefChip(ref.path, symbol || "", line ? parseInt(line, 10) : 0);
    });
  }

  function isPathishChar(ch) {
    return /[A-Za-z0-9_./\\]/.test(ch || "");
  }

  function decoratePlainText(text) {
    return String(text || "").replace(/[A-Za-z][A-Za-z0-9]{10}/g, function (match, offset, whole) {
      if (!isDhis2Uid(match)) return match;
      var before = offset > 0 ? whole.charAt(offset - 1) : "";
      var after = whole.charAt(offset + match.length) || "";
      if (isPathishChar(before) || isPathishChar(after)) return match;
      return uidChip(match);
    });
  }

  function findCloseTag(html, from, name) {
    var re = new RegExp("<\\/" + name + "\\s*>", "i");
    var slice = html.slice(from);
    var match = re.exec(slice);
    if (!match) return null;
    return {
      innerEnd: from + match.index,
      end: from + match.index + match[0].length
    };
  }

  function decorateUids(html) {
    var source = String(html || "");
    var out = "";
    var i = 0;
    while (i < source.length) {
      if (source.charAt(i) !== "<") {
        var next = source.indexOf("<", i);
        if (next < 0) next = source.length;
        out += decoratePlainText(source.slice(i, next));
        i = next;
        continue;
      }
      var gt = source.indexOf(">", i);
      if (gt < 0) {
        out += source.slice(i);
        break;
      }
      var tag = source.slice(i, gt + 1);
      var nameMatch = /^<\/?\s*([A-Za-z][A-Za-z0-9]*)/.exec(tag);
      var name = nameMatch ? nameMatch[1].toLowerCase() : "";
      var closing = /^<\//.test(tag);
      if (!closing && (name === "pre" || name === "a" || name === "button")) {
        var skipped = findCloseTag(source, gt + 1, name);
        if (!skipped) {
          out += source.slice(i);
          break;
        }
        out += source.slice(i, skipped.end);
        i = skipped.end;
        continue;
      }
      if (!closing && name === "code") {
        var codeClose = findCloseTag(source, gt + 1, "code");
        if (!codeClose) {
          out += source.slice(i);
          break;
        }
        var inner = source.slice(gt + 1, codeClose.innerEnd);
        var uid = decodeEntities(stripTags(inner)).trim();
        if (isDhis2Uid(uid) && inner.indexOf("<") < 0) {
          out += uidChip(uid);
        } else {
          out += source.slice(i, codeClose.end);
        }
        i = codeClose.end;
        continue;
      }
      out += tag;
      i = gt + 1;
    }
    return out;
  }

  function decorateFileRefs(html) {
    var source = String(html || "");
    var out = "";
    var i = 0;
    while (i < source.length) {
      if (source.charAt(i) !== "<") {
        var next = source.indexOf("<", i);
        if (next < 0) next = source.length;
        out += decorateFilePlainText(source.slice(i, next));
        i = next;
        continue;
      }
      var gt = source.indexOf(">", i);
      if (gt < 0) {
        out += source.slice(i);
        break;
      }
      var tag = source.slice(i, gt + 1);
      var nameMatch = /^<\/?\s*([A-Za-z][A-Za-z0-9]*)/.exec(tag);
      var name = nameMatch ? nameMatch[1].toLowerCase() : "";
      var closing = /^<\//.test(tag);
      if (!closing && (name === "pre" || name === "a" || name === "button")) {
        var skipped = findCloseTag(source, gt + 1, name);
        if (!skipped) {
          out += source.slice(i);
          break;
        }
        out += source.slice(i, skipped.end);
        i = skipped.end;
        continue;
      }
      if (!closing && name === "code") {
        var codeClose = findCloseTag(source, gt + 1, name);
        if (!codeClose) {
          out += source.slice(i);
          break;
        }
        var inner = decodeEntities(stripTags(source.slice(gt + 1, codeClose.innerEnd))).trim();
        var ref = parseFileRef(inner);
        if (ref) {
          var trail = matchTrailingSymbol(source, codeClose.end);
          out += fileRefChip(ref.path, trail ? trail.symbol : ref.symbol, ref.line);
          i = trail ? trail.end : codeClose.end;
          continue;
        }
        out += source.slice(i, codeClose.end);
        i = codeClose.end;
        continue;
      }
      out += tag;
      i = gt + 1;
    }
    return out;
  }

  function render(text) {
    configureMarked();
    var source = String(text == null ? "" : text);
    if (!source) return "";
    var html = parseMarkdown(source);
    html = sanitizeHtml(html);
    html = rewriteLinks(html);
    html = wrapTables(html);
    html = wrapCodeBlocks(html);
    html = highlightHtml(html);
    html = decorateUids(html);
    html = decorateFileRefs(html);
    return html;
  }

  function copyText(text) {
    if (root.navigator && root.navigator.clipboard && root.navigator.clipboard.writeText) {
      return root.navigator.clipboard.writeText(text);
    }
    return Promise.reject(new Error("clipboard unavailable"));
  }

  function enhance(rootEl) {
    if (!rootEl || !rootEl.querySelectorAll) return;
    rootEl.querySelectorAll("[data-md-copy]").forEach(function (button) {
      if (button.getAttribute("data-md-bound")) return;
      button.setAttribute("data-md-bound", "1");
      button.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        var wrap = button.closest(".climate-md-code");
        var code = wrap && wrap.querySelector("code, pre");
        var text = code ? code.textContent : "";
        copyText(text).then(function () {
          button.textContent = "Copied";
          setTimeout(function () { button.textContent = "Copy"; }, 1200);
        }).catch(function () {
          button.textContent = "Copy failed";
          setTimeout(function () { button.textContent = "Copy"; }, 1200);
        });
      });
    });
    rootEl.querySelectorAll("[data-uid-copy]").forEach(function (chip) {
      if (chip.getAttribute("data-uid-bound")) return;
      chip.setAttribute("data-uid-bound", "1");
      chip.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        var uid = String(chip.textContent || "").trim();
        copyText(uid).then(function () {
          chip.setAttribute("title", "Copied");
          chip.classList.add("is-copied");
          setTimeout(function () {
            chip.setAttribute("title", "Copy UID");
            chip.classList.remove("is-copied");
          }, 1200);
        }).catch(function () {});
      });
    });
  }

  function mount(el, text) {
    if (!el) return "";
    var html = render(text);
    el.innerHTML = html;
    enhance(el);
    return html;
  }

  root.ClimateMarkdown = {
    render: render,
    enhance: enhance,
    mount: mount,
    isDhis2Uid: isDhis2Uid,
    isRepoFilePath: isRepoFilePath,
    isSymbolName: isSymbolName,
    parseFileRef: parseFileRef,
    decorateUids: decorateUids,
    decorateFileRefs: decorateFileRefs,
    ALLOWED_TAGS: ALLOWED_TAGS,
    ALLOWED_ATTR: ALLOWED_ATTR
  };
  root.renderClimateMarkdown = render;
})(typeof window !== "undefined" ? window : globalThis);
