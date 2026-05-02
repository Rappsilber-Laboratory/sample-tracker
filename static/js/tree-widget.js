// Reusable collapsible D3 tree widget.
// Exposes window.TreeWidget.render(container, rootData, options).
//
// rootData: { name, level, total_bytes, url?, children? }
// container: CSS selector string or DOM element
// options:
//   label(node)     -> string
//   onClick(node)   -> called after collapse/expand toggle
//   levelNames      -> array of column header strings indexed by depth
//   hideRoot        -> omit the depth-0 root node (default false)
//   nodeRadius      -> default 4
//   duration        -> transition ms, default 400
//   width / height  -> SVG initial size, default fills container
//   dx / dy         -> vertical/horizontal spacing, defaults 22 / 220

(function () {
  function resolveContainer(c) {
    if (typeof c === "string") return document.querySelector(c);
    return c;
  }

  function fmtGB(bytes) {
    var gb = (bytes || 0) / 1e9;
    return gb >= 0.1 ? gb.toFixed(1) + " GB" : (bytes / 1e6).toFixed(0) + " MB";
  }

  function defaultLabel(d) {
    var name = d.data.name;
    var level = d.data.level;
    var bytes = d.data.total_bytes || 0;
    var size = fmtGB(bytes);
    var ch = d._children || d.children || [];
    if (level === "project")    return name + "  (" + ch.length + " exp, " + size + ")";
    if (level === "experiment") return name + "  (" + ch.length + " samples, " + size + ")";
    if (level === "sample")     return name + "  (" + ch.length + " files, " + size + ")";
    if (level === "file")       return name + "  " + size;
    if (level === "root")       return name;
    if (d.children || d._children) return name + " (" + d.value + ")";
    return name;
  }

  // Right-angle elbow connector: source → horizontal → vertical → horizontal → target
  function elbow(d) {
    var midY = (d.source.y + d.target.y) / 2;
    return "M" + d.source.y + "," + d.source.x
         + "H" + midY
         + "V" + d.target.x
         + "H" + d.target.y;
  }

  function render(container, rootData, options) {
    options = options || {};
    var el = resolveContainer(container);
    if (!el) throw new Error("TreeWidget: container not found");

    var label = options.label || defaultLabel;
    var onClick = options.onClick || null;
    var duration = options.duration != null ? options.duration : 400;
    var dx = options.dx || 22;
    var dy = options.dy || 220;
    var width = options.width || el.clientWidth || 1200;
    var height = options.height || el.clientHeight || 800;
    var levelNames = options.levelNames || [];
    var hideRoot = options.hideRoot || false;

    el.innerHTML = "";

    var root = d3.hierarchy(rootData);
    root.sum(function (d) { return d.children && d.children.length ? 0 : 1; });
    root.x0 = 0;
    root.y0 = 0;

    root.descendants().forEach(function (d, i) {
      d.id = i;
      d._children = d.children;
      if (d.depth >= 1 && d.children) d.children = null;
    });

    var svg = d3.select(el)
      .append("svg")
      .attr("class", "tree-widget-svg")
      .attr("width", width)
      .attr("height", height)
      .style("font", "11px sans-serif")
      .style("user-select", "none");

    var gZoom = svg.append("g");

    svg.call(
      d3.zoom()
        .scaleExtent([0.1, 4])
        .on("zoom", function (event) {
          gZoom.attr("transform", event.transform);
        })
    );

    var initialX = hideRoot ? (40 - dy) : 40;
    gZoom.attr("transform", "translate(" + initialX + ",40)");

    var gColumns = gZoom.append("g").attr("class", "tw-columns");

    var gLink = gZoom.append("g")
      .attr("fill", "none")
      .attr("stroke", "#ccc")
      .attr("stroke-width", 1);

    var gNode = gZoom.append("g")
      .attr("pointer-events", "all");

    var treeLayout = d3.tree().nodeSize([dx, dy]);

    function updateColumns(nodes) {
      var depthSet = {};
      var allX = [];
      nodes.forEach(function (d) {
        depthSet[d.depth] = true;
        allX.push(d.x);
      });

      var minX = d3.min(allX);
      var maxX = d3.max(allX);
      var depths = Object.keys(depthSet).map(Number)
        .filter(function (d) { return d >= 1; })
        .sort(function (a, b) { return a - b; });

      gColumns.selectAll("*").remove();

      depths.forEach(function (depth) {
        var colX = depth * dy;

        gColumns.append("line")
          .attr("x1", colX).attr("x2", colX)
          .attr("y1", minX - 30).attr("y2", maxX + 55)
          .attr("stroke", "#e8e8e8")
          .attr("stroke-dasharray", "5,4")
          .attr("stroke-width", 1);

        var name = levelNames[depth] || "";
        if (name) {
          gColumns.append("text")
            .attr("x", colX)
            .attr("y", maxX + 48)
            .attr("text-anchor", "middle")
            .attr("fill", "#bbb")
            .attr("font-size", "10px")
            .text(name);
        }
      });
    }

    function hasChildren(d) {
      return !!(d._children && d._children.length);
    }

    function isExpanded(d) {
      return !!(d.children && d.children.length);
    }

    function toggleNode(d) {
      if (!hasChildren(d)) return;
      if (d.children) {
        d._children = d.children;
        d.children = null;
      } else {
        d.children = d._children;
      }
    }

    function update(source) {
      treeLayout(root);

      var nodes = root.descendants();
      var links = root.links();

      var visibleNodes = hideRoot ? nodes.filter(function (d) { return d.depth > 0; }) : nodes;
      var visibleLinks = hideRoot ? links.filter(function (d) { return d.source.depth > 0; }) : links;

      updateColumns(nodes);

      // ----- nodes
      var node = gNode.selectAll("g.tw-node")
        .data(visibleNodes, function (d) { return d.id; });

      var nodeEnter = node.enter().append("g")
        .attr("class", "tw-node")
        .attr("transform", function () {
          return "translate(" + source.y0 + "," + source.x0 + ")";
        })
        .attr("fill-opacity", 0)
        .attr("stroke-opacity", 0);

      // Triangle expand/collapse indicator (only for nodes with children)
      nodeEnter.append("path")
        .attr("class", "tw-tri")
        .attr("d", "M-4,-4 L5,0 L-4,4 Z")
        .attr("cursor", "pointer")
        .on("click", function (event, d) {
          event.stopPropagation();
          toggleNode(d);
          update(d);
          if (onClick) onClick(d);
        });

      // Small dot for leaf nodes
      nodeEnter.append("circle")
        .attr("class", "tw-dot")
        .attr("r", 2.5)
        .attr("cx", 0).attr("cy", 0);

      // Label — click navigates to URL
      nodeEnter.append("text")
        .attr("class", "tw-label")
        .attr("dy", "0.31em")
        .attr("x", 10)
        .attr("text-anchor", "start")
        .attr("cursor", function (d) { return d.data.url ? "pointer" : "default"; })
        .text(label)
        .attr("paint-order", "stroke")
        .attr("stroke", "white")
        .attr("stroke-width", 3)
        .attr("stroke-linejoin", "round")
        .on("click", function (event, d) {
          event.stopPropagation();
          if (d.data.url) window.location.href = d.data.url;
        });

      var nodeUpdate = node.merge(nodeEnter).transition().duration(duration)
        .attr("transform", function (d) { return "translate(" + d.y + "," + d.x + ")"; })
        .attr("fill-opacity", 1)
        .attr("stroke-opacity", 1);

      // Triangle: visible on internal nodes, coloured by expand state
      nodeUpdate.select("path.tw-tri")
        .attr("display", function (d) { return hasChildren(d) ? null : "none"; })
        .attr("fill", function (d) { return isExpanded(d) ? "#fff" : "#3b6ea5"; })
        .attr("stroke", "#3b6ea5")
        .attr("stroke-width", 1)
        .attr("transform", function (d) { return isExpanded(d) ? "rotate(90)" : "rotate(0)"; });

      // Dot: visible on leaf nodes only
      nodeUpdate.select("circle.tw-dot")
        .attr("display", function (d) { return hasChildren(d) ? "none" : null; })
        .attr("fill", "#bbb")
        .attr("stroke", "#999")
        .attr("stroke-width", 1);

      nodeUpdate.select("text.tw-label")
        .text(label)
        .attr("fill", function (d) { return d.data.url ? "#2c5f9e" : "#444"; });

      node.exit().transition().duration(duration).remove()
        .attr("transform", function () {
          return "translate(" + source.y + "," + source.x + ")";
        })
        .attr("fill-opacity", 0)
        .attr("stroke-opacity", 0);

      // ----- links
      var link = gLink.selectAll("path.tw-link")
        .data(visibleLinks, function (d) { return d.target.id; });

      var linkEnter = link.enter().append("path")
        .attr("class", "tw-link")
        .attr("d", function () {
          var o = { x: source.x0, y: source.y0 };
          return elbow({ source: o, target: o });
        });

      link.merge(linkEnter).transition().duration(duration)
        .attr("d", elbow);

      link.exit().transition().duration(duration).remove()
        .attr("d", function () {
          var o = { x: source.x, y: source.y };
          return elbow({ source: o, target: o });
        });

      root.eachBefore(function (d) {
        d.x0 = d.x;
        d.y0 = d.y;
      });
    }

    update(root);

    return {
      root: root,
      svg: svg.node(),
      update: update
    };
  }

  window.TreeWidget = { render: render };
})();
