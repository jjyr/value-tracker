const languageCode = document.documentElement.lang || "en-US";
const languageKey = (document.documentElement.dataset.lang || languageCode).toLowerCase().startsWith("zh") ? "zh" : "en";
const languageStorageKey = "value-tracker-language";

const text = {
  en: {
    collapse: "Collapse",
    disclosed: "Disclosed {date}",
    emptyPie: "No data available.",
    expand: "Expand",
    newPositions: "New",
    portfolioWeight: "Portfolio {value}",
    reportPeriod: "Report period {period}",
    sharesValue: "{value} shares",
    site_title: "Value Tracker"
  },
  zh: {
    collapse: "收起",
    disclosed: "披露 {date}",
    emptyPie: "暂无可统计数据。",
    expand: "展开",
    newPositions: "新建",
    portfolioWeight: "组合 {value}",
    reportPeriod: "报告期 {period}",
    sharesValue: "{value} 股",
    site_title: "价值追踪"
  }
};

function t(key, params = {}) {
  const template = (text[languageKey] && text[languageKey][key]) || text.en[key] || key;
  return Object.entries(params).reduce((output, [name, value]) => output.replaceAll(`{${name}}`, value), template);
}

const moneyFormatter = new Intl.NumberFormat(languageCode, {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2
});

const percentFormatter = new Intl.NumberFormat(languageCode, {
  maximumFractionDigits: 1,
  minimumFractionDigits: 1,
  signDisplay: "always"
});

const ratioFormatter = new Intl.NumberFormat(languageCode, {
  maximumFractionDigits: 1,
  minimumFractionDigits: 1
});

const shareFormatter = new Intl.NumberFormat(languageCode, {
  maximumFractionDigits: 0
});

const defaultChartSeries = [
  { key: "spy_value", valueKey: "spy_return_pct", amountKey: "spy_value", label: "SPY", className: "line-spy", pointClass: "point-spy", color: "#67d4ff" },
  { key: "qqq_value", valueKey: "qqq_return_pct", amountKey: "qqq_value", label: "QQQ", className: "line-qqq", pointClass: "point-qqq", color: "#b69cff" }
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

function formatRatio(value) {
  return `${ratioFormatter.format(value)}%`;
}

function formatChartDate(dateValue) {
  const date = new Date(dateValue);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
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

function holdingPieTooltipElement() {
  let tooltip = document.querySelector(".holding-pie-tooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.className = "holding-pie-tooltip";
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

function polarToCartesian(cx, cy, radius, angle) {
  const radians = ((angle - 90) * Math.PI) / 180;
  return {
    x: cx + radius * Math.cos(radians),
    y: cy + radius * Math.sin(radians)
  };
}

function pieSlicePath(cx, cy, radius, startAngle, endAngle) {
  const start = polarToCartesian(cx, cy, radius, endAngle);
  const end = polarToCartesian(cx, cy, radius, startAngle);
  const largeArc = endAngle - startAngle <= 180 ? "0" : "1";
  return [
    `M ${cx} ${cy}`,
    `L ${start.x.toFixed(3)} ${start.y.toFixed(3)}`,
    `A ${radius} ${radius} 0 ${largeArc} 0 ${end.x.toFixed(3)} ${end.y.toFixed(3)}`,
    "Z"
  ].join(" ");
}

function formatPieValue(row) {
  const value = Number(row.value);
  if (!Number.isFinite(value)) return "--";
  if (row.valueKind === "money") return moneyFormatter.format(value);
  return t("sharesValue", { value: shareFormatter.format(value) });
}

function pieDetailText(row) {
  const details = [];
  if (row.company_name) details.push(row.company_name);
  const weight = Number(row.weight);
  if (Number.isFinite(weight) && weight > 0) details.push(t("portfolioWeight", { value: formatRatio(weight) }));
  const shares = Number(row.shares);
  if (Number.isFinite(shares) && shares > 0) details.push(t("sharesValue", { value: shareFormatter.format(shares) }));
  if (Array.isArray(row.managers) && row.managers.length) details.push(row.managers.join(" / "));
  return details.join(" · ") || "--";
}

function setupHoldingPies() {
  const pies = Array.from(document.querySelectorAll("[data-holding-pie]"));
  if (!pies.length) return;

  const colors = ["#54d690", "#67d4ff", "#f3c969", "#ff8a65", "#b69cff", "#5eead4", "#f472b6", "#a3e635", "#fb7185", "#38bdf8"];
  const tooltip = holdingPieTooltipElement();
  const hideTooltip = () => tooltip.classList.remove("is-visible");
  const positionTooltip = (event) => {
    const margin = 12;
    const tooltipRect = tooltip.getBoundingClientRect();
    let left = event.clientX + 14;
    if (left + tooltipRect.width > window.innerWidth - margin) {
      left = event.clientX - tooltipRect.width - 14;
    }
    let top = event.clientY - tooltipRect.height / 2;
    top = Math.max(margin, Math.min(top, window.innerHeight - tooltipRect.height - margin));
    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
  };

  pies.forEach((container) => {
    const rows = parseJsonData(container.dataset.holdingPie, [])
      .map((row, index) => ({
        ...row,
        value: Number(row.value),
        color: colors[index % colors.length]
      }))
      .filter((row) => Number.isFinite(row.value) && row.value > 0);
    const total = rows.reduce((sum, row) => sum + row.value, 0);
    if (!rows.length || total <= 0) {
      container.innerHTML = `<p class="muted">${escapeHtml(t("emptyPie"))}</p>`;
      return;
    }

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 180 180");
    svg.setAttribute("class", "holding-pie-svg");
    svg.setAttribute("role", "img");
    const legend = document.createElement("div");
    legend.className = "holding-pie-legend";
    let cursor = 0;

    rows.forEach((row, index) => {
      const share = row.value / total;
      const startAngle = cursor * 360;
      const endAngle = (cursor + share) * 360;
      cursor += share;

      const slice = document.createElementNS("http://www.w3.org/2000/svg", share >= 0.9999 ? "circle" : "path");
      if (slice.tagName.toLowerCase() === "circle") {
        slice.setAttribute("cx", "90");
        slice.setAttribute("cy", "90");
        slice.setAttribute("r", "72");
      } else {
        slice.setAttribute("d", pieSlicePath(90, 90, 72, startAngle, endAngle));
      }
      slice.setAttribute("class", "holding-pie-slice");
      slice.setAttribute("fill", row.color);
      slice.setAttribute("tabindex", "0");
      slice.setAttribute("aria-label", `${row.symbol} ${formatRatio(share * 100)}`);
      svg.appendChild(slice);

      const showTooltip = (event) => {
        tooltip.innerHTML = `<strong>${escapeHtml(row.symbol)}</strong><span>${formatRatio(share * 100)} · ${escapeHtml(formatPieValue(row))}</span><em>${escapeHtml(pieDetailText(row))}</em>`;
        tooltip.classList.add("is-visible");
        positionTooltip(event);
      };
      slice.addEventListener("mouseenter", showTooltip);
      slice.addEventListener("mousemove", positionTooltip);
      slice.addEventListener("mouseleave", hideTooltip);
      slice.addEventListener("focus", (event) => showTooltip({ clientX: window.innerWidth / 2, clientY: window.innerHeight / 2, ...event }));
      slice.addEventListener("blur", hideTooltip);

      const item = document.createElement("button");
      item.type = "button";
      item.className = "holding-pie-legend-item";
      item.innerHTML = `<i style="--pie-color:${row.color}"></i><span>${escapeHtml(row.symbol)}</span><strong>${formatRatio(share * 100)}</strong>`;
      item.addEventListener("mouseenter", (event) => showTooltip(event));
      item.addEventListener("mousemove", positionTooltip);
      item.addEventListener("mouseleave", hideTooltip);
      item.addEventListener("focus", (event) => showTooltip({ clientX: window.innerWidth / 2, clientY: window.innerHeight / 2, ...event }));
      item.addEventListener("blur", hideTooltip);
      legend.appendChild(item);
    });

    container.innerHTML = "";
    container.appendChild(svg);
    container.appendChild(legend);
  });
}

function chartSeriesFor(svg) {
  const series = parseJsonData(svg.dataset.series, defaultChartSeries);
  return (Array.isArray(series) && series.length ? series : defaultChartSeries).map((item, index) => ({
    ...item,
    key: item.key || `series_${index}`,
    valueKey: item.valueKey || "return_pct",
    amountKey: item.amountKey || "value",
    label: localizedSeriesLabel(item, index),
    pointClass: item.pointClass || `point-series-${index}`,
    className: item.className || "",
    color: item.color || ["#54d690", "#67d4ff", "#b69cff", "#f3c969", "#ff8a65", "#7dd3fc"][index % 6]
  }));
}

function localizedSeriesLabel(item, index) {
  if (item.label_key) return t(item.label_key);
  if (languageKey === "zh" && item.label_zh) return item.label_zh;
  if (languageKey === "en" && item.label_en) return item.label_en;
  return item.label || item.key || `Series ${index + 1}`;
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

function numericField(point, key) {
  if (!key) return NaN;
  const value = Number(point[key]);
  return Number.isFinite(value) ? value : NaN;
}

function seriesPointList(series, basePoints) {
  const rawPoints = Array.isArray(series.points)
    ? series.points
    : basePoints;
  return rawPoints
    .map((point) => {
      const value = numericField(point, series.valueKey);
      const amount = numericField(point, series.amountKey);
      return {
        ...point,
        dateMs: dateMs(point.date),
        value,
        amount
      };
    })
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
    label.textContent = formatPercent(tickValue);
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
  firstLabel.textContent = formatChartDate(minDateMs).slice(5);
  svg.appendChild(firstLabel);

  const lastLabel = document.createElementNS(namespace, "text");
  lastLabel.setAttribute("x", width - right);
  lastLabel.setAttribute("y", height - 8);
  lastLabel.setAttribute("text-anchor", "end");
  lastLabel.setAttribute("class", "chart-label");
  lastLabel.textContent = formatChartDate(maxDateMs).slice(5);
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
      const pct = point.value;
      const amount = Number.isFinite(point.amount) ? moneyFormatter.format(point.amount) : "";
      const disclosure = point.dateMs === hoverDateMs ? "" : t("disclosed", { date: point.date.slice(5) });
      const details = [amount, disclosure].filter(Boolean).join(" · ");
      const newPositions = point.dateMs === hoverDateMs && hasNewPositionEvents(point) ? point.new_positions : [];
      const eventPeriod = point.event_report_period ? ` · ${escapeHtml(t("reportPeriod", { period: point.event_report_period }))}` : "";
      const newPositionText = newPositions.length
        ? `<div class="chart-tooltip-event">${escapeHtml(t("newPositions"))} ${newPositions.slice(0, 4).map((event) => escapeHtml(event.symbol)).join(" / ")}${eventPeriod}</div>`
        : "";
      return `<div><span class="chart-tooltip-name" style="color:${item.color}">${escapeHtml(item.label)}</span><strong>${formatPercent(pct)}</strong><span>${details}</span></div>${newPositionText}`;
    });
    hoverGroup.setAttribute("visibility", "visible");

    tooltip.innerHTML = `<time>${formatChartDate(hoverDateMs)}</time>${rows.join("")}`;
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
  button.querySelector("span").textContent = expanded ? t("collapse") : t("expand");
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

function setupHoldingPeriodSelector() {
  const options = Array.from(document.querySelectorAll("[data-holding-period-option]"));
  if (!options.length) return;
  const panels = Array.from(document.querySelectorAll("[data-holding-period-panel]"));
  const summary = document.querySelector("[data-holding-period-summary]");
  const render = (selected) => {
    const key = selected.dataset.holdingPeriodOption;
    options.forEach((option) => {
      const active = option === selected;
      option.classList.toggle("is-active", active);
      option.setAttribute("aria-pressed", String(active));
    });
    panels.forEach((panel) => {
      panel.hidden = panel.dataset.holdingPeriodPanel !== key;
    });
    if (summary && selected) {
      summary.textContent = selected.dataset.periodLabel || "--";
    }
    const tooltip = document.querySelector(".holding-pie-tooltip");
    if (tooltip) tooltip.classList.remove("is-visible");
    formatMoneyElements();
    formatShareElements();
  };
  options.forEach((option) => {
    option.addEventListener("click", () => render(option));
  });
  render(options.find((option) => option.classList.contains("is-active")) || options[0]);
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

function setupLanguageSwitcher() {
  const options = Array.from(document.querySelectorAll("[data-language-option]"));
  if (!options.length) return;
  let basePath = document.documentElement.dataset.basePath || "/";
  if (!basePath.endsWith("/")) basePath += "/";
  const path = window.location.pathname;
  if (!path.startsWith(basePath)) return;

  const relativePath = path.slice(basePath.length);
  const isZhPath = relativePath === "zh" || relativePath.startsWith("zh/");
  const unprefixedPath = isZhPath ? relativePath.replace(/^zh\/?/, "") : relativePath;
  const suffix = `${window.location.search}${window.location.hash}`;

  options.forEach((option) => {
    const targetLang = option.dataset.languageOption;
    const targetPath = targetLang === "zh" ? `${basePath}zh/${unprefixedPath}` : `${basePath}${unprefixedPath}`;
    option.href = `${targetPath}${suffix}`;
    option.addEventListener("click", () => {
      if (targetLang === "zh" || targetLang === "en") {
        localStorage.setItem(languageStorageKey, targetLang);
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupLanguageSwitcher();
  formatMoneyElements();
  formatShareElements();
  setupChartControls();
  setupRankingFilters();
  setupRankingToggles();
  setupHoldingPeriodSelector();
  setupRebalancePagination();
  setupHoldingPies();
  setupTooltips();
});
