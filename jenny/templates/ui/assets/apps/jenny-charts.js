/* ── Jenny Charts — tiny chart helpers for Jenny Apps, built on global d3 ──
   Load d3 first: <script src="/html-mobile/assets/vendor/d3@7/d3.min.js">
   All colors come from the Jenny Kit CSS variables so charts follow the theme.
   API:
     JennyCharts.line(el, points)        points: [{x: Date|number|string, y: number}]
     JennyCharts.bars(el, items)         items:  [{label, value}]
     JennyCharts.gauge(el, value, max)   single level/percentage */
(function () {
  'use strict';

  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function palette() {
    return {
      // Riserve allineate a quelle di jenny-kit.css (chanel): un indaco che
      // nessun tema ha renderebbe muto un token mancante invece che evidente.
      accent: cssVar('--accent', '#f4f1ea'),
      subtle: cssVar('--accent-subtle', 'rgba(244,241,234,0.15)'),
      text2: cssVar('--text2', '#8b8b96'),
      text3: cssVar('--text3', '#52525e'),
      border: cssVar('--border2', 'rgba(255,255,255,0.1)'),
      heading: cssVar('--heading', '#e4e4e7'),
    };
  }

  function svgIn(el, height) {
    const width = el.clientWidth || 300;
    d3.select(el).selectAll('svg').remove();
    const svg = d3.select(el).append('svg')
      .attr('viewBox', `0 0 ${width} ${height}`)
      .attr('width', '100%')
      .attr('height', height);
    return { svg, width };
  }

  function line(el, points, opts = {}) {
    const height = opts.height || 160;
    const m = { top: 10, right: 10, bottom: 22, left: 34 };
    const { svg, width } = svgIn(el, height);
    const c = palette();
    if (!points || !points.length) return;

    const data = points.map(p => ({
      x: p.x instanceof Date ? p.x : (typeof p.x === 'number' ? p.x : new Date(p.x)),
      y: +p.y,
    })).filter(p => !Number.isNaN(p.y));
    const timeScale = data[0].x instanceof Date;
    const x = (timeScale ? d3.scaleTime() : d3.scaleLinear())
      .domain(d3.extent(data, d => d.x))
      .range([m.left, width - m.right]);
    const y = d3.scaleLinear()
      .domain([Math.min(0, d3.min(data, d => d.y)), d3.max(data, d => d.y)]).nice()
      .range([height - m.bottom, m.top]);

    svg.append('g')
      .attr('transform', `translate(0,${height - m.bottom})`)
      .call(d3.axisBottom(x).ticks(4).tickSize(0).tickPadding(8))
      .call(g => g.select('.domain').attr('stroke', c.border))
      .selectAll('text').attr('fill', c.text3).style('font-size', '10px');
    svg.append('g')
      .attr('transform', `translate(${m.left},0)`)
      .call(d3.axisLeft(y).ticks(4).tickSize(-(width - m.left - m.right)).tickPadding(6))
      .call(g => g.select('.domain').remove())
      .call(g => g.selectAll('.tick line').attr('stroke', c.border))
      .selectAll('text').attr('fill', c.text3).style('font-size', '10px');

    svg.append('path').datum(data)
      .attr('fill', c.subtle)
      .attr('d', d3.area().x(d => x(d.x)).y0(y(Math.max(0, y.domain()[0]))).y1(d => y(d.y))
        .curve(d3.curveMonotoneX));
    svg.append('path').datum(data)
      .attr('fill', 'none')
      .attr('stroke', c.accent)
      .attr('stroke-width', 2)
      .attr('d', d3.line().x(d => x(d.x)).y(d => y(d.y)).curve(d3.curveMonotoneX));
  }

  function bars(el, items, opts = {}) {
    const height = opts.height || 160;
    const m = { top: 10, right: 6, bottom: 24, left: 30 };
    const { svg, width } = svgIn(el, height);
    const c = palette();
    if (!items || !items.length) return;

    const x = d3.scaleBand()
      .domain(items.map(d => d.label))
      .range([m.left, width - m.right])
      .padding(0.3);
    const y = d3.scaleLinear()
      .domain([0, d3.max(items, d => +d.value)]).nice()
      .range([height - m.bottom, m.top]);

    svg.append('g')
      .attr('transform', `translate(${m.left},0)`)
      .call(d3.axisLeft(y).ticks(3).tickSize(-(width - m.left - m.right)).tickPadding(6))
      .call(g => g.select('.domain').remove())
      .call(g => g.selectAll('.tick line').attr('stroke', c.border))
      .selectAll('text').attr('fill', c.text3).style('font-size', '10px');

    svg.selectAll('rect').data(items).join('rect')
      .attr('x', d => x(d.label))
      .attr('y', d => y(+d.value))
      .attr('width', x.bandwidth())
      .attr('height', d => y(0) - y(+d.value))
      .attr('rx', 4)
      .attr('fill', c.accent);

    svg.append('g')
      .attr('transform', `translate(0,${height - m.bottom})`)
      .call(d3.axisBottom(x).tickSize(0).tickPadding(8))
      .call(g => g.select('.domain').remove())
      .selectAll('text').attr('fill', c.text2).style('font-size', '10px');
  }

  function gauge(el, value, max = 100, opts = {}) {
    const size = opts.size || Math.min(el.clientWidth || 160, 160);
    const { svg } = svgIn(el, size);
    const c = palette();
    const ratio = Math.max(0, Math.min(1, max ? value / max : 0));
    const r = size / 2 - 8;
    const arcSpan = Math.PI * 1.5;
    const start = -arcSpan / 2;
    const g = svg.attr('viewBox', `0 0 ${size} ${size}`)
      .append('g').attr('transform', `translate(${size / 2},${size / 2})`);

    const arc = (from, to) => d3.arc()
      .innerRadius(r - 9).outerRadius(r)
      .cornerRadius(9)
      .startAngle(from).endAngle(to)();
    g.append('path').attr('d', arc(start, start + arcSpan)).attr('fill', c.border);
    if (ratio > 0) {
      g.append('path').attr('d', arc(start, start + arcSpan * ratio)).attr('fill', c.accent);
    }
    g.append('text')
      .attr('text-anchor', 'middle').attr('dy', '0.1em')
      .attr('fill', c.heading)
      .style('font-size', `${size / 4.2}px`).style('font-weight', 650)
      .text(opts.label != null ? opts.label : Math.round(ratio * 100) + '%');
  }

  window.JennyCharts = { line, bars, gauge };
})();
