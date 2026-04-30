// Reusable collapsible D3 tree widget.
// Exposes window.TreeWidget.render(container, rootData, options).
//
// rootData: { name: string, children?: rootData[], ...arbitrary fields }
// container: CSS selector string or DOM element
// options:
//   label(node)     -> string (default: "<name> (<leafCount>)" for internal, name for leaves)
//   onClick(node)   -> called after toggle; useful for lazy-loading more children
//   nodeRadius      -> default 4
//   duration        -> transition ms, default 400
//   width / height  -> SVG initial size, default fills container
//   dx / dy         -> vertical/horizontal spacing, defaults 22 / 180
//
// The widget makes no assumption about what the tree represents.

(function () {
  function resolveContainer(c) {
    if (typeof c === "string") return document.querySelector(c);
    return c;
  }

  function defaultLabel(d) {
    if (d.children || d._children) {
      // .value is set by hierarchy.sum() — counts leaf descendants
      return d.data.name + " (" + d.value + ")";
    }
    return d.data.name;
  }

  function render(container, rootData, options) {
    options = options || {};
    var el = resolveContainer(container);
    if (!el) throw new Error("TreeWidget: container not found");

    var label = options.label || defaultLabel;
    var onClick = options.onClick || null;
    var nodeRadius = options.nodeRadius || 4;
    var duration = options.duration != null ? options.duration : 400;
    var dx = options.dx || 22;
    var dy = options.dy || 200;
    var width = options.width || el.clientWidth || 1200;
    var height = options.height || el.clientHeight || 800;

    // Clear container
    el.innerHTML = "";

    var root = d3.hierarchy(rootData);
    root.sum(function (d) { return d.children && d.children.length ? 0 : 1; });
    root.x0 = 0;
    root.y0 = 0;

    // Collapse everything except the root's first level — start compact.
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
      .style("font", "12px sans-serif")
      .style("user-select", "none");

    var gZoom = svg.append("g");

    svg.call(
      d3.zoom()
        .scaleExtent([0.1, 4])
        .on("zoom", function (event) {
          gZoom.attr("transform", event.transform);
        })
    );

    // Initial offset so the root sits a bit in from the left edge
    gZoom.attr("transform", "translate(40,40)");

    var gLink = gZoom.append("g")
      .attr("fill", "none")
      .attr("stroke", "#999")
      .attr("stroke-opacity", 0.5)
      .attr("stroke-width", 1.2);

    var gNode = gZoom.append("g")
      .attr("cursor", "pointer")
      .attr("pointer-events", "all");

    var treeLayout = d3.tree().nodeSize([dx, dy]);
    var diagonal = d3.linkHorizontal()
      .x(function (d) { return d.y; })
      .y(function (d) { return d.x; });

    function update(source) {
      treeLayout(root);

      var nodes = root.descendants();
      var links = root.links();

      // ----- nodes
      var node = gNode.selectAll("g.tw-node")
        .data(nodes, function (d) { return d.id; });

      var nodeEnter = node.enter().append("g")
        .attr("class", "tw-node")
        .attr("transform", function () {
          return "translate(" + source.y0 + "," + source.x0 + ")";
        })
        .attr("fill-opacity", 0)
        .attr("stroke-opacity", 0)
        .on("click", function (event, d) {
          if (d._children) {
            if (d.children) {
              d._children = d.children;
              d.children = null;
            } else {
              d.children = d._children;
            }
          }
          update(d);
          if (onClick) onClick(d);
        });

      nodeEnter.append("circle")
        .attr("r", nodeRadius)
        .attr("fill", function (d) { return d._children ? "#3b6ea5" : "#bbb"; })
        .attr("stroke", "#fff")
        .attr("stroke-width", 1.5);

      nodeEnter.append("text")
        .attr("dy", "0.31em")
        .attr("x", function (d) { return d._children ? -8 : 8; })
        .attr("text-anchor", function (d) { return d._children ? "end" : "start"; })
        .text(label)
        .attr("paint-order", "stroke")
        .attr("stroke", "white")
        .attr("stroke-width", 3)
        .attr("stroke-linejoin", "round");

      var nodeUpdate = node.merge(nodeEnter).transition().duration(duration)
        .attr("transform", function (d) { return "translate(" + d.y + "," + d.x + ")"; })
        .attr("fill-opacity", 1)
        .attr("stroke-opacity", 1);

      nodeUpdate.select("circle")
        .attr("fill", function (d) {
          if (!d._children) return "#bbb";        // leaf
          return d.children ? "#fff" : "#3b6ea5"; // open vs collapsed
        })
        .attr("stroke", function (d) { return d._children ? "#3b6ea5" : "#999"; });

      nodeUpdate.select("text").text(label);

      node.exit().transition().duration(duration).remove()
        .attr("transform", function () {
          return "translate(" + source.y + "," + source.x + ")";
        })
        .attr("fill-opacity", 0)
        .attr("stroke-opacity", 0);

      // ----- links
      var link = gLink.selectAll("path.tw-link")
        .data(links, function (d) { return d.target.id; });

      var linkEnter = link.enter().append("path")
        .attr("class", "tw-link")
        .attr("d", function () {
          var o = { x: source.x0, y: source.y0 };
          return diagonal({ source: o, target: o });
        });

      link.merge(linkEnter).transition().duration(duration)
        .attr("d", diagonal);

      link.exit().transition().duration(duration).remove()
        .attr("d", function () {
          var o = { x: source.x, y: source.y };
          return diagonal({ source: o, target: o });
        });

      // Stash old positions for transition origins
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
