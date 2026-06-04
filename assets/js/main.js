const moneyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2
});

const percentFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 1,
  minimumFractionDigits: 1,
  signDisplay: "always"
});

const shareFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0
});

const defaultChartSeries = [
  { key: "value", label: "Value Tracker", className: "line-portfolio", pointClass: "point-portfolio", color: "#54d690" },
  { key: "spy_value", label: "SPY", className: "line-spy", pointClass: "point-spy", color: "#67d4ff" },
  { key: "qqq_value", label: "QQQ", className: "line-qqq", pointClass: "point-qqq", color: "#b69cff" }
];

function formatMoneyElements() {
  document.querySelectorAll("[data-money]").forEach((element) => {
    const value = Number(element.dataset.money);
    if (!Number.isFinite(value)) {
      element.textContent = "--";
      return;
    }
    element.textContent = moneyFormatter.format(value);
  });
}

function formatShareElements() {
  document.querySelectorAll("[data-shares]").forEach((element) => {
    const value = Number(element.dataset.shares);
    if (!Number.isFinite(value)) {
      element.textContent = "--";
      return;
    }
    element.textContent = shareFormatter.format(value);
  });
}

function formatPercent(value) {
  return `${percentFormatter.format(value)}%`;
}

function chartTooltipElement() {
  let tooltip = document.querySelector(".chart-hover-tooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.className = "chart-hover-tooltip";
    document.body.appendChild(tooltip);
  }
  return tooltip;
}

function parseJsonData(raw, fallback) {
  try {
    const parsed = JSON.parse(raw || "");
    return parsed || fallback;
  } catch {
    return fallback;
  }
}

function chartSeriesFor(svg) {
  const series = parseJsonData(svg.dataset.series, defaultChartSeries);
  return (Array.isArray(series) && series.length ? series : defaultChartSeries).map((item, index) => ({
    ...item,
    key: item.key || `series_${index}`,
    label: item.label || item.key || `Series ${index + 1}`,
    pointClass: item.pointClass || `point-series-${index}`,
    className: item.className || "",
    color: item.color || ["#54d690", "#67d4ff", "#b69cff", "#f3c969", "#ff8a65", "#7dd3fc"][index % 6]
  }));
}

function activeChartSeries(svg) {
  const series = chartSeriesFor(svg);
  const activeKeys = (svg.dataset.activeSeries || series.map((item) => item.key).join(",")).split(",");
  const active = series.filter((item) => activeKeys.includes(item.key));
  return active.length ? active : series;
}

function setActiveChartSeries(svg, keys) {
  svg.dataset.activeSeries = keys.join(",");
}

function dateMs(value) {
  return new Date(`${value}T00:00:00`).getTime();
}

function seriesPointList(series, basePoints) {
  const rawPoints = Array.isArray(series.points)
    ? series.points
    : basePoints.map((point) => ({ ...point, value: point[series.key] }));
  return rawPoints
    .map((point) => ({
      ...point,
      dateMs: dateMs(point.date),
      value: Number(point.value)
    }))
    .filter((point) => point.date && Number.isFinite(point.dateMs) && Number.isFinite(point.value))
    .sort((a, b) => a.dateMs - b.dateMs);
}

function filterChartRange(points, range, latestMs) {
  const ranges = { "1m": 31, "3m": 93, all: Infinity };
  const days = ranges[range] || Infinity;
  return points.filter((point) => {
    if (range === "ytd") {
      const latest = new Date(latestMs);
      return point.dateMs >= new Date(latest.getFullYear(), 0, 1).getTime();
    }
    if (!Number.isFinite(days)) return true;
    return (latestMs - point.dateMs) / 86400000 <= days;
  });
}

function pointOnOrBefore(points, targetMs) {
  if (!points.length) return null;
  return points.reduce((latest, point) => (point.dateMs <= targetMs ? point : latest), null);
}

function hasNewPositionEvents(point) {
  return Array.isArray(point.new_positions) && point.new_positions.length > 0;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;");
}

function svgClientX(svg, event, fallbackWidth) {
  const matrix = svg.getScreenCTM();
  if (matrix && typeof svg.createSVGPoint === "function") {
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(matrix.inverse()).x;
  }
  const rect = svg.getBoundingClientRect();
  return ((event.clientX - rect.left) * fallbackWidth) / rect.width;
}

function drawEquityChart(svg) {
  const points = parseJsonData(svg.dataset.points, []);
  if (!Array.isArray(points)) return;

  const range = svg.dataset.range || "ytd";
  const activeSeries = activeChartSeries(svg);
  const allSeriesPoints = activeSeries.map((item) => ({ item, points: seriesPointList(item, points) }));
  const latestMs = Math.max(...allSeriesPoints.flatMap((entry) => entry.points.map((point) => point.dateMs)));
  if (!Number.isFinite(latestMs)) return;
  const visibleSeries = allSeriesPoints
    .map((entry) => ({ ...entry, points: filterChartRange(entry.points, range, latestMs) }))
    .filter((entry) => entry.points.length);
  const drawableSeries = visibleSeries.length ? visibleSeries : allSeriesPoints.filter((entry) => entry.points.length);
  const values = drawableSeries.flatMap((entry) => entry.points.map((point) => point.value)).filter(Number.isFinite);
  if (!values.length) return;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.14, 1);
  const low = min - pad;
  const high = max + pad;
  const baseline = Number(svg.dataset.baseline) || 100000;
  const width = 760;
  const height = 260;
  const left = 58;
  const right = 22;
  const top = 18;
  const bottom = 34;
  const innerWidth = width - left - right;
  const innerHeight = height - top - bottom;
  const minDateMs = Math.min(...drawableSeries.flatMap((entry) => entry.points.map((point) => point.dateMs)));
  const maxDateMs = Math.max(...drawableSeries.flatMap((entry) => entry.points.map((point) => point.dateMs)));

  const x = (dateValue) => {
    if (maxDateMs === minDateMs) return left + innerWidth / 2;
    return left + (innerWidth * (dateValue - minDateMs)) / (maxDateMs - minDateMs);
  };
  const y = (value) => top + innerHeight - ((value - low) / (high - low)) * innerHeight;
  const pathFor = (seriesPoints) =>
    seriesPoints
      .map((point, index) => `${index === 0 ? "M" : "L"}${x(point.dateMs).toFixed(1)},${y(point.value).toFixed(1)}`)
      .join(" ");

  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = "";
  const namespace = "http://www.w3.org/2000/svg";

  [0, 1, 2, 3].forEach((tick) => {
    const tickValue = high - ((high - low) * tick) / 3;
    const line = document.createElementNS(namespace, "line");
    const yy = top + (innerHeight * tick) / 3;
    line.setAttribute("x1", left);
    line.setAttribute("x2", width - right);
    line.setAttribute("y1", yy);
    line.setAttribute("y2", yy);
    line.setAttribute("class", "chart-grid-line");
    svg.appendChild(line);

    const label = document.createElementNS(namespace, "text");
    label.setAttribute("x", left - 10);
    label.setAttribute("y", yy + 4);
    label.setAttribute("text-anchor", "end");
    label.setAttribute("class", "chart-axis-label");
    label.textContent = formatPercent((tickValue / baseline - 1) * 100);
    svg.appendChild(label);
  });

  drawableSeries.forEach(({ item, points: seriesPoints }) => {
    const path = document.createElementNS(namespace, "path");
    path.setAttribute("d", pathFor(seriesPoints));
    path.setAttribute("class", `chart-line ${item.className}`);
    path.style.stroke = item.color;
    svg.appendChild(path);
  });

  drawableSeries.forEach(({ item, points: seriesPoints }) => {
    seriesPoints.filter(hasNewPositionEvents).forEach((point) => {
      const circle = document.createElementNS(namespace, "circle");
      circle.setAttribute("r", "3.4");
      circle.setAttribute("cx", x(point.dateMs));
      circle.setAttribute("cy", y(point.value));
      circle.setAttribute("class", "chart-event-dot");
      circle.style.fill = item.color;
      svg.appendChild(circle);
    });
  });

  const firstLabel = document.createElementNS(namespace, "text");
  firstLabel.setAttribute("x", left);
  firstLabel.setAttribute("y", height - 8);
  firstLabel.setAttribute("class", "chart-label");
  firstLabel.textContent = new Date(minDateMs).toISOString().slice(5, 10);
  svg.appendChild(firstLabel);

  const lastLabel = document.createElementNS(namespace, "text");
  lastLabel.setAttribute("x", width - right);
  lastLabel.setAttribute("y", height - 8);
  lastLabel.setAttribute("text-anchor", "end");
  lastLabel.setAttribute("class", "chart-label");
  lastLabel.textContent = new Date(maxDateMs).toISOString().slice(5, 10);
  svg.appendChild(lastLabel);

  const hoverGroup = document.createElementNS(namespace, "g");
  hoverGroup.setAttribute("class", "chart-hover-layer");
  hoverGroup.setAttribute("visibility", "hidden");
  const hoverLine = document.createElementNS(namespace, "line");
  hoverLine.setAttribute("class", "chart-hover-line");
  hoverLine.setAttribute("y1", top);
  hoverLine.setAttribute("y2", height - bottom);
  hoverGroup.appendChild(hoverLine);
  const hoverDots = drawableSeries.map(({ item }) => {
    const circle = document.createElementNS(namespace, "circle");
    circle.setAttribute("r", "4.2");
    circle.setAttribute("class", `chart-point ${item.pointClass}`);
    circle.style.fill = item.color;
    hoverGroup.appendChild(circle);
    return { item, circle };
  });
  svg.appendChild(hoverGroup);

  const overlay = document.createElementNS(namespace, "rect");
  overlay.setAttribute("x", left);
  overlay.setAttribute("y", top);
  overlay.setAttribute("width", innerWidth);
  overlay.setAttribute("height", innerHeight);
  overlay.setAttribute("class", "chart-hover-capture");
  svg.appendChild(overlay);

  const tooltip = chartTooltipElement();
  const hoverDates = Array.from(new Set(drawableSeries.flatMap((entry) => entry.points.map((point) => point.dateMs)))).sort((a, b) => a - b);
  const renderHover = (event) => {
    const scaledX = svgClientX(svg, event, width);
    const ratio = Math.max(0, Math.min(1, (scaledX - left) / innerWidth));
    const targetMs = minDateMs + ratio * (maxDateMs - minDateMs);
    const hoverDateMs = hoverDates.reduce((best, date) => (Math.abs(date - targetMs) < Math.abs(best - targetMs) ? date : best), hoverDates[0]);
    const xx = x(hoverDateMs);

    hoverLine.setAttribute("x1", xx);
    hoverLine.setAttribute("x2", xx);
    const rows = drawableSeries.map((entry, index) => {
      const point = pointOnOrBefore(entry.points, hoverDateMs);
      const { item } = entry;
      const circle = hoverDots[index].circle;
      if (!point) {
        circle.setAttribute("visibility", "hidden");
        return "";
      }
      circle.setAttribute("visibility", "visible");
      circle.setAttribute("cx", x(point.dateMs));
      circle.setAttribute("cy", y(point.value));
      const pct = (point.value / baseline - 1) * 100;
      const extra = Number.isFinite(Number(point.return_pct)) ? ` · 持仓 ${formatPercent(Number(point.return_pct))}` : "";
      const disclosure = point.dateMs === hoverDateMs ? "" : ` · 披露 ${point.date.slice(5)}`;
      const newPositions = point.dateMs === hoverDateMs && hasNewPositionEvents(point) ? point.new_positions : [];
      const eventPeriod = point.event_report_period ? ` · 报告期 ${escapeHtml(point.event_report_period)}` : "";
      const newPositionText = newPositions.length
        ? `<div class="chart-tooltip-event">新建 ${newPositions.slice(0, 4).map((event) => escapeHtml(event.symbol)).join(" / ")}${eventPeriod}</div>`
        : "";
      return `<div><span class="chart-tooltip-name" style="color:${item.color}">${escapeHtml(item.label)}</span><strong>${formatPercent(pct)}</strong><span>${moneyFormatter.format(point.value)}${extra}${disclosure}</span></div>${newPositionText}`;
    });
    hoverGroup.setAttribute("visibility", "visible");

    tooltip.innerHTML = `<time>${new Date(hoverDateMs).toISOString().slice(0, 10)}</time>${rows.join("")}`;
    tooltip.classList.add("is-visible");

    const tooltipRect = tooltip.getBoundingClientRect();
    const margin = 12;
    let leftPx = event.clientX + 14;
    if (leftPx + tooltipRect.width > window.innerWidth - margin) {
      leftPx = event.clientX - tooltipRect.width - 14;
    }
    let topPx = event.clientY - tooltipRect.height / 2;
    topPx = Math.max(margin, Math.min(topPx, window.innerHeight - tooltipRect.height - margin));
    tooltip.style.left = `${Math.round(leftPx)}px`;
    tooltip.style.top = `${Math.round(topPx)}px`;
  };

  overlay.addEventListener("pointermove", renderHover);
  overlay.addEventListener("pointerleave", () => {
    hoverGroup.setAttribute("visibility", "hidden");
    tooltip.classList.remove("is-visible");
  });
}

function setupChartControls() {
  document.querySelectorAll(".equity-chart").forEach((chart) => {
    const panel = chart.closest(".chart-panel") || document;
    const series = chartSeriesFor(chart);
    chart.dataset.range = chart.dataset.range || "ytd";
    setActiveChartSeries(chart, series.map((item) => item.key));
    panel.querySelectorAll(".range-button").forEach((button) => {
      const active = button.dataset.range === chart.dataset.range;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    drawEquityChart(chart);
    panel.querySelectorAll(".range-button").forEach((button) => {
      button.addEventListener("click", () => {
        panel.querySelectorAll(".range-button").forEach((item) => {
          item.classList.remove("is-active");
          item.setAttribute("aria-pressed", "false");
        });
        button.classList.add("is-active");
        button.setAttribute("aria-pressed", "true");
        chart.dataset.range = button.dataset.range;
        drawEquityChart(chart);
      });
    });
    panel.querySelectorAll(".legend-toggle").forEach((button) => {
      button.addEventListener("click", () => {
        const current = new Set((chart.dataset.activeSeries || "").split(",").filter(Boolean));
        const key = button.dataset.series;
        if (current.has(key) && current.size > 1) {
          current.delete(key);
        } else {
          current.add(key);
        }
        setActiveChartSeries(chart, Array.from(current));
        panel.querySelectorAll(".legend-toggle").forEach((item) => {
          const active = current.has(item.dataset.series);
          item.classList.toggle("is-active", active);
          item.setAttribute("aria-pressed", String(active));
        });
        drawEquityChart(chart);
      });
    });
  });
}

function activeRankingBody(panel) {
  return panel.querySelector("tbody[data-ranking-scope]:not([hidden])") || panel.querySelector("tbody");
}

function renderRankingExpansion(panel, expanded) {
  const button = panel.querySelector(".ranking-toggle");
  const body = activeRankingBody(panel);
  if (!button || !body) return;
  const extraRows = body.querySelectorAll(".extra-row");
  button.hidden = !extraRows.length;
  button.setAttribute("aria-expanded", String(expanded));
  button.querySelector("span").textContent = expanded ? "收起" : "展开";
  extraRows.forEach((row) => row.classList.toggle("is-hidden", !expanded));
}

function setupRankingFilters() {
  document.querySelectorAll(".ranking-panel").forEach((panel) => {
    panel.querySelectorAll("[data-ranking-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        const scope = button.dataset.rankingFilter;
        panel.querySelectorAll("[data-ranking-filter]").forEach((item) => {
          const active = item.dataset.rankingFilter === scope;
          item.classList.toggle("is-active", active);
          item.setAttribute("aria-pressed", String(active));
        });
        panel.querySelectorAll("[data-ranking-scope]").forEach((body) => {
          body.hidden = body.dataset.rankingScope !== scope;
        });
        renderRankingExpansion(panel, false);
        formatMoneyElements();
        formatShareElements();
      });
    });
    renderRankingExpansion(panel, false);
  });
}

function setupRankingToggles() {
  document.querySelectorAll(".ranking-panel").forEach((panel) => {
    const button = panel.querySelector(".ranking-toggle");
    if (!button) return;
    button.addEventListener("click", () => {
      const expanded = button.getAttribute("aria-expanded") === "true";
      renderRankingExpansion(panel, !expanded);
    });
  });
}

function setupRebalancePagination() {
  const list = document.querySelector("[data-rebalance-list]");
  if (!list) return;
  const items = Array.from(list.querySelectorAll("[data-rebalance-item]"));
  const pageSize = Math.max(Number(list.dataset.pageSize) || 5, 1);
  const totalPages = Math.max(Math.ceil(items.length / pageSize), 1);
  const prev = document.querySelector("[data-rebalance-prev]");
  const next = document.querySelector("[data-rebalance-next]");
  const indicator = document.querySelector("[data-rebalance-page]");
  let page = 0;

  const render = () => {
    items.forEach((item, index) => {
      const visible = index >= page * pageSize && index < (page + 1) * pageSize;
      item.classList.toggle("is-hidden", !visible);
    });
    if (indicator) indicator.textContent = `${page + 1} / ${totalPages}`;
    if (prev) prev.disabled = page === 0;
    if (next) next.disabled = page >= totalPages - 1;
  };

  if (prev) {
    prev.addEventListener("click", () => {
      page = Math.max(page - 1, 0);
      render();
    });
  }
  if (next) {
    next.addEventListener("click", () => {
      page = Math.min(page + 1, totalPages - 1);
      render();
    });
  }
  render();
}

function setupTooltips() {
  const triggers = Array.from(document.querySelectorAll("[data-tooltip]"));
  if (!triggers.length) return;

  const tooltip = document.createElement("div");
  tooltip.className = "floating-tooltip";
  tooltip.setAttribute("role", "tooltip");
  document.body.appendChild(tooltip);

  let activeTrigger = null;

  const positionTooltip = () => {
    if (!activeTrigger) return;
    const rect = activeTrigger.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    const gap = 10;
    const margin = 12;
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    let left = rect.left + rect.width / 2 - tooltipRect.width / 2;
    left = Math.max(margin, Math.min(left, viewportWidth - tooltipRect.width - margin));

    let placement = "top";
    let top = rect.top - tooltipRect.height - gap;
    if (top < margin) {
      placement = "bottom";
      top = rect.bottom + gap;
    }
    top = Math.max(margin, Math.min(top, viewportHeight - tooltipRect.height - margin));

    const arrowLeft = rect.left + rect.width / 2 - left;
    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
    tooltip.style.setProperty("--arrow-left", `${Math.round(arrowLeft)}px`);
    tooltip.dataset.placement = placement;
  };

  const showTooltip = (trigger) => {
    const text = trigger.dataset.tooltip;
    if (!text) return;
    activeTrigger = trigger;
    tooltip.textContent = text;
    tooltip.classList.add("is-visible");
    requestAnimationFrame(positionTooltip);
  };

  const hideTooltip = () => {
    activeTrigger = null;
    tooltip.classList.remove("is-visible");
  };

  triggers.forEach((trigger) => {
    trigger.addEventListener("mouseenter", () => showTooltip(trigger));
    trigger.addEventListener("focus", () => showTooltip(trigger));
    trigger.addEventListener("mouseleave", hideTooltip);
    trigger.addEventListener("blur", hideTooltip);
  });

  window.addEventListener("scroll", positionTooltip, true);
  window.addEventListener("resize", positionTooltip);
}

document.addEventListener("DOMContentLoaded", () => {
  formatMoneyElements();
  formatShareElements();
  setupChartControls();
  setupRankingFilters();
  setupRankingToggles();
  setupRebalancePagination();
  setupTooltips();
});
