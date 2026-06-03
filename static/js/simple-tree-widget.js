// Collapsible tree widget — no external dependencies.
// Exposes window.TreeWidget.render(container, rootData, options).
//
// rootData: { name, level, total_bytes, url?, children? }
// container: CSS selector string or DOM element
// options:
//   label(node)     -> string  (receives plain data object, not d3 node)
//   onClick(node)   -> called after collapse/expand toggle
//   levelNames      -> array of column header strings indexed by depth
//   hideRoot        -> omit depth-0 root node (default false)

(function () {
  function resolveContainer(c) {
    return typeof c === "string" ? document.querySelector(c) : c;
  }

  function fmtGB(bytes) {
    var gb = (bytes || 0) / 1e9;
    return gb >= 0.1 ? gb.toFixed(1) + " GB" : (bytes / 1e6).toFixed(0) + " MB";
  }

  function childCount(node) {
    return node.children ? node.children.length : 0;
  }

  function countDescendants(data, targetLevel) {
    if (!data.children || !data.children.length) return 0;
    var count = 0;
    data.children.forEach(function (c) {
      if (c.level === targetLevel) count++;
      count += countDescendants(c, targetLevel);
    });
    return count;
  }

  function defaultLabel(node) {
    var name = node.name;
    var level = node.level;
    var bytes = node.total_bytes || 0;
    var size = fmtGB(bytes);
    if (level === "project") {
      var exps = childCount(node);
      var samples = countDescendants(node, "sample");
      var files = countDescendants(node, "file");
      return name + " (" + exps + " experiments · " + samples + " samples · " + files + " files, " + size + ")";
    }
    if (level === "experiment") {
      var samples = childCount(node);
      var files = countDescendants(node, "file");
      return name + " (" + samples + " samples · " + files + " files, " + size + ")";
    }
    if (level === "sample") {
      var files = childCount(node);
      return name + " (" + files + " files, " + size + ")";
    }
    if (level === "file")  return name + "  " + size;
    if (level === "root")  return name;
    var n = childCount(node);
    if (n > 0)             return name + " (" + n + ")";
    return name;
  }

  // Recursively build the tree. Each node carries:
  //   .data       — original data object
  //   .children   — null (collapsed) or array of child nodes
  //   ._children  — full child array when collapsed
  //   .depth
  function buildTree(data, depth) {
    var node = { data: data, depth: depth, children: null, _children: null };
    if (data.children && data.children.length) {
      node._children = data.children.map(function (c) { return buildTree(c, depth + 1); });
    }
    return node;
  }

  function render(container, rootData, options) {
    options = options || {};
    var el = resolveContainer(container);
    if (!el) throw new Error("TreeWidget: container not found");

    var labelFn = options.label || defaultLabel;
    var onClickFn = options.onClick || null;
    var levelNames = options.levelNames || [];
    var hideRoot = options.hideRoot || false;

    el.innerHTML = "";

    var root = buildTree(rootData, 0);

    // Expand first level by default (mirrors original behaviour)
    if (root._children) {
      root.children = root._children;
    }

    // --- styles (injected once per page) ---
    if (!document.getElementById("stw-style")) {
      var style = document.createElement("style");
      style.id = "stw-style";
      style.textContent = [
        /* force light scheme regardless of OS dark mode */
        "#tree-container { background: #fff; color: #222; }",
        ".stw-tree { font: 15px/1.6 sans-serif; color: #222; padding: 8px 0; }",
        ".stw-tree ul { list-style: none; margin: 0; padding: 0 0 0 22px; }",
        ".stw-tree > ul { padding-left: 0; }",
        ".stw-tree li { margin: 2px 0; }",
        ".stw-row { display: flex; align-items: center; gap: 6px; padding: 3px 6px;",
        "           border-radius: 3px; cursor: default; background: #fff; }",
        ".stw-row:hover { background: #eef3fb; }",
        ".stw-toggle { width: 18px; height: 18px; display: flex; align-items: center;",
        "              justify-content: center; flex-shrink: 0; cursor: pointer;",
        "              color: #3b6ea5; font-size: 12px; user-select: none; }",
        ".stw-toggle:hover { color: #1a4a88; }",
        ".stw-dot { width: 7px; height: 7px; border-radius: 50%; background: #ccc;",
        "           border: 1px solid #aaa; flex-shrink: 0; margin-left: 5px; }",
        ".stw-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #222; }",
        ".stw-label.linkable { color: #1a4a9e; cursor: pointer; text-decoration: none; }",
        ".stw-label.linkable:hover { text-decoration: underline; }",
        ".stw-children { overflow: hidden; transition: max-height 0.2s ease; }",
        ".stw-header { display: flex; gap: 0; margin-bottom: 6px;",
        "              border-bottom: 1px solid #dde; padding-bottom: 5px; background: #fff; }",
        ".stw-header-cell { font-size: 12px; color: #666; font-weight: 600;",
        "                   letter-spacing: 0.04em; text-transform: uppercase; }",
      ].join("\n");
      document.head.appendChild(style);
    }

    // --- level name header ---
    // var names = levelNames.filter(function (n) { return n; });
    // if (names.length) {
    //   var header = document.createElement("div");
    //   header.className = "stw-header";
    //   names.forEach(function (name) {
    //     var cell = document.createElement("span");
    //     cell.className = "stw-header-cell";
    //     cell.style.marginRight = "12px";
    //     cell.textContent = name;
    //     header.appendChild(cell);
    //   });
    //   el.appendChild(header);
    // }

    // --- tree wrapper ---
    var wrapper = document.createElement("div");
    wrapper.className = "stw-tree";
    var rootList = document.createElement("ul");
    wrapper.appendChild(rootList);
    el.appendChild(wrapper);

    function makeRow(node) {
      var li = document.createElement("li");

      var row = document.createElement("div");
      row.className = "stw-row";

      // expand/collapse toggle or leaf dot
      if (node._children && node._children.length) {
        var toggle = document.createElement("span");
        toggle.className = "stw-toggle";
        toggle.setAttribute("aria-label", "toggle");

        var childrenDiv = document.createElement("div");
        childrenDiv.className = "stw-children";

        function setToggleIcon() {
          toggle.textContent = node.children ? "▾" : "▸";
        }
        setToggleIcon();

        toggle.addEventListener("click", function (e) {
          e.stopPropagation();
          if (node.children) {
            childrenDiv.style.maxHeight = "0";
            node.children = null;
          } else {
            node.children = node._children;
            renderChildren(childrenDiv, node);
            // Set max-height after children rendered so transition works
            childrenDiv.style.maxHeight = childrenDiv.scrollHeight + "px";
            // Allow growth after transition ends
            childrenDiv.addEventListener("transitionend", function once() {
              if (node.children) childrenDiv.style.maxHeight = "none";
              childrenDiv.removeEventListener("transitionend", once);
            });
          }
          setToggleIcon();
          if (onClickFn) onClickFn(node);
        });

        row.appendChild(toggle);
        li.appendChild(row);
        li.appendChild(childrenDiv);

        // Render initially expanded children
        if (node.children) {
          renderChildren(childrenDiv, node);
          childrenDiv.style.maxHeight = "none";
        } else {
          childrenDiv.style.maxHeight = "0";
        }
      } else {
        var dot = document.createElement("span");
        dot.className = "stw-dot";
        row.appendChild(dot);
        li.appendChild(row);
      }

      // label — real <a> when there's a URL so right-click "open in new tab" works
      var label;
      if (node.data.url) {
        label = document.createElement("a");
        label.href = node.data.url;
        label.classList.add("linkable");
      } else {
        label = document.createElement("span");
      }
      label.className = (label.className ? label.className + " " : "") + "stw-label";
      label.textContent = labelFn(node.data);
      if (node.data.level && node.data.level !== "root") {
        label.title = node.data.level.charAt(0).toUpperCase() + node.data.level.slice(1);
      }
      row.appendChild(label);

      return li;
    }

    function renderChildren(container, node) {
      container.innerHTML = "";
      var ul = document.createElement("ul");
      (node.children || []).forEach(function (child) {
        ul.appendChild(makeRow(child));
      });
      container.appendChild(ul);
    }

    var startNodes = (hideRoot && root._children) ? root._children : [root];
    startNodes.forEach(function (node) {
      rootList.appendChild(makeRow(node));
    });
  }

  window.TreeWidget = { render: render };
})();
