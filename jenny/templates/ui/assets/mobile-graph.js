import { api } from './shared/api-client.js';
import { escapeHtml, hashString } from './shared/utils.js';
import { i18n } from './shared/i18n.js';

export class GraphController {
  constructor() {
    this.svgEl = document.getElementById('graph-svg');
    this.loadingEl = document.getElementById('graph-loading');
    this.graphWrapEl = document.getElementById('graph-wrap');
    this.currentWiki = null;
    this.teardown = null;
    // Azzeratore del focus sul nodo, installato da renderWikiGraph (v. lì).
    this._clearFocus = null;
    // Token di caricamento (monotono): solo l'ultimo loadGraph disegna.
    this._loadToken = 0;
    // Contatore di generazione: incrementato in deactivate(), permette a una
    // continuazione di scoprire che la *sezione* è stata abbandonata. Il token
    // da solo non basta: è cieco all'uscita dal grafo.
    this._gen = 0;
    // Il grafo si ridisegna comunque a ogni activate() (rientro in vista), e le
    // etichette della legenda sono data-i18n statiche già ri-tradotte da
    // _applyStaticTranslations(). Questo listener copre solo il caso in cui la
    // lingua cambi mentre il grafo è già la vista attiva.
    i18n.onLocaleChange(() => {
      if (window.mobileApp?.currentMode === 'graph') {
        this.loadGraph(this.currentWiki, false);
      }
    });
  }

  showLoading() {
    if (this.loadingEl) this.loadingEl.classList.add('active');
  }

  hideLoading() {
    if (this.loadingEl) this.loadingEl.classList.remove('active');
  }

  activate() {
    // Sorgente unica del caricamento. Chi porta qui una vista precisa (il
    // popstate, il boot, il pulsante Grafo dell'header) la deposita in
    // `mobileApp` con requestGraph e la carica questo activate(): prima ogni
    // chiamante rifaceva `loadGraph` subito dopo `switchMode`, cioè due volte.
    // Senza richiesta si ricarica la vista corrente (landing di default sul
    // grafo overview, non su una wiki specifica).
    const pending = window.mobileApp?.takePendingGraph?.() || null;
    if (pending) this.loadGraph(pending.wiki, pending.push === true);
    else this.loadGraph(this.currentWiki, false);
  }

  deactivate() {
    // Le continuazioni di loadGraph nate prima dell'uscita dalla sezione devono
    // poter scoprire di essere state superate: il token non le copre, perché è
    // monotono solo rispetto ad altri caricamenti *del grafo*.
    this._gen++;
    this._cleanup();
    this.hideLoading();
  }

  /** true se questo caricamento è stato superato da uno più recente o se nel
   *  frattempo si è usciti dalla sezione. */
  _stale(token, gen) {
    return token !== this._loadToken || gen !== this._gen;
  }

  _cleanup() {
    // Il focus vive nel grafo che sta per essere smontato: tenerne
    // l'azzeratore significherebbe far consumare una pressione di Indietro a
    // una closure che opera su nodi non più a schermo.
    this._clearFocus = null;
    if (this.teardown) {
      this.teardown();
      this.teardown = null;
    }
  }

  async loadGraph(wiki = null, pushHistory = true) {
    // Catturati prima del primo await: la fetch del grafo può essere superata
    // da un altro caricamento o dall'uscita dalla sezione, e disegnare dopo
    // significherebbe sovrascrivere il grafo di qualcun altro (o quello di
    // nessuno) e impilare una entry di history per una schermata mai vista.
    const token = ++this._loadToken;
    const gen = this._gen;
    this.currentWiki = wiki;
    this._cleanup();
    this.showLoading();

    try {
      const data = await api.getGraph(wiki);
      if (this._stale(token, gen)) return;
      if (wiki) {
        this.renderWikiGraph(data, wiki);
      } else {
        this.renderHomeGraph(data);
      }

      if (pushHistory) {
        window.mobileApp.pushNav({ mode: 'graph', wiki });
      }

      this._updateTitle(wiki);
      this._renderBreadcrumbs(wiki);
    } catch (err) {
      if (this._stale(token, gen)) return;
      console.error('Failed to load graph:', err);
      this.svgEl.innerHTML = `<text x="50%" y="50%" text-anchor="middle" fill="#666" font-size="12">${i18n.t('graph.failedToLoad')}</text>`;
    } finally {
      // Chi è stato superato non spegne lo spinner di chi lo ha superato.
      if (!this._stale(token, gen)) this.hideLoading();
    }
  }

  _updateTitle(wiki) {
    const title = wiki ? `Graph: ${wiki}` : i18n.t('graph.wikis');
    if (window.mobileApp?.header) {
      window.mobileApp.header.setTitle(title, 'graph');
    }
  }

  _renderBreadcrumbs(wiki) {
    if (!this.graphWrapEl) return;
    let existing = this.graphWrapEl.querySelector('.wiki-breadcrumbs');
    if (existing) existing.remove();

    const wrap = document.createElement('div');
    wrap.className = 'wiki-breadcrumbs graph-breadcrumbs';

    if (!wiki) {
      wrap.innerHTML = `<span class="bc-current">${i18n.t('graph.wikis')}</span>`;
    } else {
      wrap.innerHTML = `
        <a class="bc-link" data-home href="/?mode=graph">${i18n.t('graph.wikis')}</a>
        <span class="bc-sep">/</span>
        <span class="bc-current">${escapeHtml(wiki)}</span>
      `;
    }

    this.graphWrapEl.insertBefore(wrap, this.graphWrapEl.firstChild);

    wrap.querySelectorAll('a[data-home]').forEach(a => {
      a.addEventListener('click', (e) => {
        e.preventDefault();
        this.loadGraph(null);
      });
    });
  }

  renderHomeGraph(data) {
    // `this.teardown` va **invocato**, non solo riassegnato: la riassegnazione
    // in fondo a questo metodo dimenticava la simulazione d3 precedente senza
    // fermarla, e quella continuava a ticchettare per sempre su nodi non più a
    // schermo. `_cleanup()` è idempotente, quindi ripeterlo dopo quello di
    // loadGraph non costa niente e copre ogni altro chiamante.
    this._cleanup();
    const svg = d3.select(this.svgEl);
    svg.selectAll('*').remove();

    // Nella vista home i colori dei nodi sono per-wiki (non semantici):
    // la legenda concepts/entities/... non si applica → nascosta.
    const legend = document.getElementById('graph-legend');
    if (legend) legend.style.display = 'none';

    const width = this.svgEl.clientWidth || 400;
    const height = this.svgEl.clientHeight || 600;
    svg.attr('viewBox', `0 0 ${width} ${height}`);

    const root = svg.append('g');
    const linkLayer = root.append('g');
    const nodeLayer = root.append('g');
    const labelLayer = root.append('g');  // sopra i nodi

    const cx = width / 2;
    const cy = height / 2;

    const allNodes = data.nodes.map(n => ({ ...n }));
    const allLinks = data.edges.map(e => ({ ...e }));

    const homeNode = allNodes.find(n => n.id === '_home');
    const wikiNodes = allNodes.filter(n => n.id !== '_home');

    if (homeNode) {
      homeNode.x = cx;
      homeNode.y = cy;
      homeNode.fx = cx;
      homeNode.fy = cy;
    }

    const starRadius = Math.min(width, height) * 0.32;
    wikiNodes.forEach((n, i) => {
      const angle = (i / Math.max(wikiNodes.length, 1)) * 2 * Math.PI - Math.PI / 2;
      n.x = cx + starRadius * Math.cos(angle);
      n.y = cy + starRadius * Math.sin(angle);
    });

    const WIKI_PALETTE = ['var(--accent)','var(--ok)','var(--error)','var(--orange, #f59e0b)','var(--cyan, #06b6d4)','var(--purple, #a855f7)','var(--pink, #ec4899)','var(--teal, #14b8a6)','var(--amber, #f97316)','var(--lime, #84cc16)'];
    wikiNodes.forEach((n, i) => { n._color = WIKI_PALETTE[i % WIKI_PALETTE.length]; });

    const nodeMap = new Map(allNodes.map(n => [n.id, n]));
    const links = allLinks
      .map(e => ({
        source: nodeMap.get(e.source),
        target: nodeMap.get(e.target)
      }))
      .filter(l => l.source && l.target);

    const wikiRadiusFn = (n) => {
      if (n.id === '_home') return 14 + Math.sqrt(n.degree || 0) * 0.4;
      return 16;
    };

    const sim = d3.forceSimulation(allNodes)
      .force('link', d3.forceLink(links).id(d => d.id).distance(130).strength(0.5))
      .force('charge', d3.forceManyBody().strength(-260).distanceMax(500))
      .force('center', d3.forceCenter(cx, cy).strength(0.02))
      .force('collision', d3.forceCollide().radius(d => wikiRadiusFn(d) + 8).strength(0.9))
      .alphaDecay(0.04)
      .velocityDecay(0.35);

    // Layout statico: converge prima di disegnare (niente animazione a riposo).
    settleSimulation(sim);

    const linkSel = linkLayer.selectAll('line').data(links).enter().append('line')
      .attr('class', 'link')
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);

    const nodeSel = nodeLayer.selectAll('g.node').data(allNodes).enter().append('g')
      .attr('class', d => `node group-${sanitizeGroup(d.group)}${d.id === '_home' ? ' big' : ''}`)
      .attr('transform', d => `translate(${d.x},${d.y})`);

    nodeSel.append('circle').attr('class', 'node-main')
      .attr('r', d => wikiRadiusFn(d))
      .style('fill', d => d._color || null);
    nodeSel.append('title').text(d => nodeLabelText(d));

    // Poche etichette nella vista home: sempre visibili (classe .big).
    const labelSel = labelLayer.selectAll('text.node-label').data(allNodes).enter().append('text')
      .attr('class', 'node-label big')
      .attr('text-anchor', 'middle')
      .attr('dy', d => d.id === '_home' ? 4 : wikiRadiusFn(d) + 12)
      .attr('x', d => d.x)
      .attr('y', d => d.y)
      .text(d => truncateLabel(nodeLabelText(d)));

    sim.on('tick', () => {
      linkSel
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y);
      nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
      labelSel.attr('x', d => d.x).attr('y', d => d.y);
    });

    const dragBehavior = d3.drag()
      .on('start', (event, d) => {
        if (!event.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on('drag', (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on('end', (event, d) => {
        if (!event.active) sim.alphaTarget(0);
        if (d.id !== '_home') {
          d.fx = null;
          d.fy = null;
        }
      });

    nodeSel.call(dragBehavior);

    nodeSel
      .style('cursor', d => d.id === '_home' ? 'default' : 'pointer')
      .on('click', (event, d) => {
        event.stopPropagation();
        if (d.id !== '_home') {
          this.loadGraph(d.id, true);
        }
      });

    const zoomBehavior = d3.zoom()
      .scaleExtent([0.3, 4])
      .on('zoom', (event) => root.attr('transform', event.transform));
    svg.call(zoomBehavior);

    this.teardown = () => {
      sim.stop();
      svg.selectAll('*').remove();
    };
  }

  renderWikiGraph(data, wiki) {
    // Come in renderHomeGraph: la simulazione precedente si ferma, non si
    // dimentica (v. il commento lì).
    this._cleanup();
    const svg = d3.select(this.svgEl);
    svg.selectAll('*').remove();

    // Vista wiki: i nodi sono colorati per gruppo semantico → legenda visibile.
    const legend = document.getElementById('graph-legend');
    if (legend) legend.style.display = '';

    const width = this.svgEl.clientWidth || 400;
    const height = this.svgEl.clientHeight || 600;
    svg.attr('viewBox', `0 0 ${width} ${height}`);

    const root = svg.append('g');
    const linkLayer = root.append('g');
    const nodeLayer = root.append('g');
    const labelLayer = root.append('g');  // sopra i nodi: label sempre in primo piano

    const EXCLUDE_ID = 'wiki/index.md';

    const allNodes = data.nodes.map(n => ({ ...n }));
    const allLinks = data.edges.map(e => ({ ...e }));

    const adjacency = new Map();
    for (const n of allNodes) adjacency.set(n.id, new Set());
    for (const e of allLinks) {
      if (!adjacency.has(e.source) || !adjacency.has(e.target)) continue;
      adjacency.get(e.source).add(e.target);
      adjacency.get(e.target).add(e.source);
    }

    const radius = (n) => 6 + Math.sqrt(n.degree || 0) * 2.6;

    const cx = width / 2;
    const cy = height / 2;

    const bfsOrder = new Map();
    const queue = [EXCLUDE_ID];
    bfsOrder.set(EXCLUDE_ID, 0);
    let bfsIdx = 1;
    while (queue.length > 0) {
      const current = queue.shift();
      const currentNeighbors = adjacency.get(current);
      if (!(currentNeighbors instanceof Set)) continue;
      const neighbors = [...currentNeighbors].sort((a, b) => hashString(a) - hashString(b));
      for (const neighbor of neighbors) {
        if (!bfsOrder.has(neighbor)) {
          bfsOrder.set(neighbor, bfsIdx++);
          queue.push(neighbor);
        }
      }
    }

    const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
    const SPACING = 22;

    for (const n of allNodes) {
      if (n.id === EXCLUDE_ID) {
        n.x = cx;
        n.y = cy;
        continue;
      }
      const idx = bfsOrder.get(n.id) ?? (bfsIdx + hashString(n.id) % 1000);
      const theta = idx * GOLDEN_ANGLE + (hashString(n.id) % 1000) / 1000 * 0.3;
      const r = SPACING * Math.sqrt(idx);
      n.x = cx + r * Math.cos(theta);
      n.y = cy + r * Math.sin(theta);
    }

    const nodeMap = new Map(allNodes.map(n => [n.id, n]));
    const nodes = allNodes.filter(n => n.id !== EXCLUDE_ID);
    const visibleIds = new Set(nodes.map(n => n.id));
    const links = allLinks
      .filter(e => e.source !== EXCLUDE_ID && e.target !== EXCLUDE_ID
        && visibleIds.has(e.source) && visibleIds.has(e.target))
      .map(e => ({
        source: nodeMap.get(e.source),
        target: nodeMap.get(e.target)
      }))
      .filter(l => l.source && l.target);

    const sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(d => d.id).distance(90).strength(0.35))
      .force('charge', d3.forceManyBody().strength(-180).distanceMax(500))
      .force('center', d3.forceCenter(cx, cy).strength(0.008))
      .force('collision', d3.forceCollide().radius(d => radius(d) + 6).strength(0.85))
      .alphaDecay(0.04)
      .velocityDecay(0.35);

    // Layout statico: converge prima di disegnare (niente animazione a riposo).
    settleSimulation(sim);

    const linkSel = linkLayer.selectAll('line').data(links).enter().append('line')
      .attr('class', 'link')
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);

    const nodeSel = nodeLayer.selectAll('g.node').data(nodes).enter().append('g')
      .attr('class', d => `node group-${sanitizeGroup(d.group)}`)
      .attr('transform', d => `translate(${d.x},${d.y})`);

    nodeSel.append('circle').attr('class', 'node-main').attr('r', d => radius(d));
    nodeSel.append('title').text(d => nodeLabelText(d));  // tooltip: titolo completo

    // Ogni nodo ha la sua etichetta: è declutterLabels() a spegnere solo quelle
    // che si sovrappongono, non una soglia sul numero di collegamenti.
    const labelSel = labelLayer.selectAll('text.node-label').data(nodes).enter().append('text')
      .attr('class', d => `node-label group-${sanitizeGroup(d.group)}`)
      .attr('text-anchor', 'middle')
      .attr('dy', d => -radius(d) - 8)
      .attr('x', d => d.x)
      .attr('y', d => d.y)
      .text(d => truncateLabel(nodeLabelText(d)));

    declutterLabels(labelSel);

    // Durante il drag i nodi si spostano: il declutter va rifatto, ma throttlato
    // (è O(n²) sul numero di etichette) e ripetuto a simulazione ferma.
    let lastDeclutter = 0;
    sim.on('tick', () => {
      linkSel
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y);
      nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
      labelSel.attr('x', d => d.x).attr('y', d => d.y);
      const now = performance.now();
      if (now - lastDeclutter > 150) {
        lastDeclutter = now;
        declutterLabels(labelSel);
      }
    });
    sim.on('end', () => declutterLabels(labelSel));

    const dragBehavior = d3.drag()
      .on('start', (event, d) => {
        if (!event.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on('drag', (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on('end', (event, d) => {
        if (!event.active) sim.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });

    nodeSel.call(dragBehavior);

    const zoomBehavior = d3.zoom()
      .scaleExtent([0.2, 4])
      .on('zoom', (event) => root.attr('transform', event.transform));

    svg.call(zoomBehavior);

    let focusNodeId = null;
    let hoverNodeId = null;

    function getNeighbors(id) {
      return adjacency.get(id) || new Set();
    }

    function computeExtent(targetNodes) {
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const n of targetNodes) {
        minX = Math.min(minX, n.x);
        minY = Math.min(minY, n.y);
        maxX = Math.max(maxX, n.x);
        maxY = Math.max(maxY, n.y);
      }
      const pad = 60;
      return [[minX - pad, minY - pad], [maxX + pad, maxY + pad]];
    }

    function zoomToExtent(targetNodes, duration = 750) {
      const [[x0, y0], [x1, y1]] = computeExtent(targetNodes);
      const w = x1 - x0, h = y1 - y0;
      const k = Math.min(width / w, height / h, 4) * 0.9;
      const tx = width / 2 - k * (x0 + x1) / 2;
      const ty = height / 2 - k * (y0 + y1) / 2;
      svg.transition().duration(duration).call(
        zoomBehavior.transform,
        d3.zoomIdentity.translate(tx, ty).scale(k)
      );
    }

    function updateVisualState() {
      let activeNodeIds = new Set(nodes.map(n => n.id));
      if (focusNodeId) {
        const focusNeighbors = getNeighbors(focusNodeId);
        activeNodeIds = new Set([focusNodeId, ...focusNeighbors]);
      }

      const highlightedNode = hoverNodeId || focusNodeId;

      if (highlightedNode && activeNodeIds.has(highlightedNode)) {
        const neighbors = getNeighbors(highlightedNode);
        const litNodes = new Set([highlightedNode, ...neighbors]);

        nodeSel
          .classed('dim', n => !litNodes.has(n.id))
          .classed('highlight', n => litNodes.has(n.id))
          .selectAll('.node-main')
          .attr('r', n => {
            if (n.id === highlightedNode) return radius(n) * 1.4;
            if (litNodes.has(n.id)) return radius(n) * 1.15;
            return radius(n);
          });

        labelSel
          .classed('dim', n => !litNodes.has(n.id))
          .classed('highlight', n => litNodes.has(n.id));

        linkSel
          .classed('dim', l => !litNodes.has(l.source.id) || !litNodes.has(l.target.id))
          .classed('highlight', l => {
            const sLit = litNodes.has(l.source.id);
            const tLit = litNodes.has(l.target.id);
            return sLit && tLit && (l.source.id === highlightedNode || l.target.id === highlightedNode);
          });
      } else {
        nodeSel
          .classed('dim', n => !activeNodeIds.has(n.id))
          .classed('highlight', false)
          .selectAll('.node-main')
          .attr('r', d => radius(d));

        labelSel
          .classed('dim', n => !activeNodeIds.has(n.id))
          .classed('highlight', false);

        linkSel
          .classed('dim', l => {
            return !activeNodeIds.has(l.source.id) || !activeNodeIds.has(l.target.id);
          })
          .classed('highlight', false);
      }
    }

    nodeSel
      .on('mouseenter', (event, d) => {
        let activeNodeIds = focusNodeId
          ? new Set([focusNodeId, ...getNeighbors(focusNodeId)])
          : new Set(nodes.map(n => n.id));
        if (!activeNodeIds.has(d.id)) return;
        hoverNodeId = d.id;
        updateVisualState();
      })
      .on('mouseleave', () => {
        hoverNodeId = null;
        updateVisualState();
      })
      .on('click', (event, d) => {
        event.stopPropagation();
        let activeNodeIds = focusNodeId
          ? new Set([focusNodeId, ...getNeighbors(focusNodeId)])
          : new Set(nodes.map(n => n.id));
        if (!activeNodeIds.has(d.id)) return;
        if (focusNodeId === d.id) return;
        focusNodeId = d.id;
        hoverNodeId = null;
        updateVisualState();
        const focusNodes = nodes.filter(n => n.id === d.id || getNeighbors(d.id).has(n.id));
        zoomToExtent(focusNodes);
      })
      .on('dblclick', (event, d) => {
        event.stopPropagation();
        const pagePath = d.path;
        if (!pagePath) return;
        window.mobileApp.switchMode('wiki', false);
        window.mobileApp.controllers.wiki.loadWikiPage(this.currentWiki, pagePath, true);
      });

    /* Uscita dal focus su un nodo. Era una closure locale, e l'unico modo di
       uscirne era azzeccare il tap su una zona vuota dell'SVG — che a grafo
       zoomato può non esistere. Promossa a stato del controller, così anche il
       tasto Indietro (handleBack) la trova. Ritorna false se non c'era focus. */
    this._clearFocus = () => {
      if (!focusNodeId) return false;
      focusNodeId = null;
      hoverNodeId = null;
      updateVisualState();
      zoomToExtent(nodes);
      return true;
    };

    /* Il listener è attaccato al nodo `#graph-svg`, che è statico: sopravvive sia
       a `svg.selectAll('*').remove()` sia al cambio di sezione, mentre
       `this._clearFocus` viene azzerato da `_cleanup()`. Il tap sullo sfondo
       dopo un teardown (o durante la fetch che lo segue) deve quindi essere un
       no-op, non un TypeError. */
    svg.on('click', () => { this._clearFocus?.(); });

    zoomToExtent(nodes, 0);

    this.teardown = () => {
      sim.stop();
      svg.selectAll('*').remove();
    };
  }

  /* Sotto-stato della sezione: il focus su un nodo è una schermata a sé (il
     grafo è zoomato sul nodo e sui suoi vicini, il resto è spento). Uscirne
     viene prima di uscire dalla sezione. */
  handleBack() {
    return this._clearFocus ? this._clearFocus() : false;
  }

  handleAction(action) {
    if (action === 'refresh') {
      this.loadGraph(this.currentWiki, false);
    }
  }
}

function sanitizeGroup(g) {
  if (['concepts', 'entities', 'summaries', 'home', 'wiki'].includes(g)) return g;
  return 'other';
}

// Testo dell'etichetta: precedenza uniforme title → label → id.
function nodeLabelText(d) {
  return d.title || d.label || d.id || '';
}

// Tronca le etichette lunghe (il titolo completo resta nel tooltip <title>).
function truncateLabel(s, max = 22) {
  if (!s) return '';
  return s.length > max ? s.slice(0, max - 1) + '…' : s;
}

// Misura una volta il rettangolo dell'etichetta e memorizza l'offset rispetto al
// nodo, così i ricalcoli successivi sono pura aritmetica (nessun reflow SVG).
// Il fallback stimato copre il caso in cui getBBox() non misuri (SVG non ancora
// renderizzato): meglio una larghezza approssimata che zero, che farebbe passare
// tutte le etichette come "non sovrapposte".
function measureLabel(el, d) {
  if (d._lw !== undefined) return;
  let box = null;
  try {
    box = el.getBBox();
  } catch {
    box = null;
  }
  if (box && box.width > 0) {
    d._lw = box.width;
    d._lh = box.height;
    d._lox = box.x - d.x;
    d._loy = box.y - d.y;
  } else {
    const chars = (el.textContent || '').length;
    d._lw = chars * 6;
    d._lh = 11;
    d._lox = -d._lw / 2;
    d._loy = Number(el.getAttribute('dy') || 0) - d._lh;
  }
}

// Mostra il nome di ogni nodo e ne nasconde uno solo quando il suo rettangolo si
// sovrappone a un'etichetta già piazzata. La priorità va ai nodi più collegati:
// in una contesa vince l'hub, che resta quindi sempre etichettato.
// Nota: le etichette stanno dentro il gruppo trasformato dallo zoom, quindi il
// testo scala insieme alle posizioni e le sovrapposizioni non dipendono dal
// livello di zoom — non serve ricalcolare a ogni pinch.
function declutterLabels(labelSel) {
  const items = [];
  labelSel.each(function (d) {
    measureLabel(this, d);
    items.push(d);
  });
  items.sort((a, b) => (b.degree || 0) - (a.degree || 0) || hashString(a.id) - hashString(b.id));

  const PAD = 3;  // margine attorno al testo (l'alone di stroke è 2.5px)
  const placed = [];
  const shown = new Set();
  for (const d of items) {
    const x0 = d.x + d._lox - PAD;
    const y0 = d.y + d._loy - PAD;
    const x1 = x0 + d._lw + PAD * 2;
    const y1 = y0 + d._lh + PAD * 2;
    if (placed.some(r => x0 < r[2] && x1 > r[0] && y0 < r[3] && y1 > r[1])) continue;
    placed.push([x0, y0, x1, y1]);
    shown.add(d.id);
  }
  labelSel.classed('big', d => shown.has(d.id));
}

// Fa convergere la simulazione off-screen e la congela: il grafo viene
// disegnato già assestato e inquadrato, senza fly-apart né drift a riposo.
function settleSimulation(sim, ticks = 300) {
  sim.stop();
  for (let i = 0; i < ticks; i++) sim.tick();
  sim.alpha(0);
}
