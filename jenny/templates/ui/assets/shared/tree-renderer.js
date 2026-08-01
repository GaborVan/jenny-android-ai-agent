/** Shared Tree Renderer — DOM-based tree construction. */

import { escapeHtml } from './utils.js';

const FOLDER_OPEN_SVG = '<svg class="tree-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
const FOLDER_CLOSED_SVG = '<svg class="tree-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
const FILE_MD_SVG = '<svg class="tree-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/><path d="M9 13v6"/><path d="M12 16l-3-3 3-3"/></svg>';

export function renderTreeNode(name, path, type, depth) {
  const indent = depth * 14;
  const div = document.createElement('div');
  div.className = 'tree-item';

  if (type === 'file') {
    div.className = 'tree-node tree-file';
    div.style.paddingLeft = `${6 + indent}px`;
    div.dataset.path = path;
    div.innerHTML = `<span class="tree-icon icon-md">${FILE_MD_SVG}</span><span class="tree-label">${escapeHtml(name)}</span>`;
    return div;
  }

  div.className = 'tree-folder collapsed';
  const node = document.createElement('div');
  node.className = 'tree-node tree-dir';
  node.style.paddingLeft = `${6 + indent}px`;
  node.dataset.path = path;
  node.innerHTML = `<span class="tree-chevron">\u203a</span><span class="tree-icon icon-folder">${FOLDER_CLOSED_SVG}</span><span class="tree-label">${escapeHtml(name)}</span>`;
  div.appendChild(node);

  const childrenDiv = document.createElement('div');
  childrenDiv.className = 'tree-children';
  div.appendChild(childrenDiv);
  return div;
}

export function wireTreeFolder(folderDiv, options) {
  const toggle = (e) => {
    e.stopPropagation();
    const isCollapsed = folderDiv.classList.contains('collapsed');
    folderDiv.classList.toggle('collapsed');
    const icon = folderDiv.querySelector('.tree-node .tree-icon');
    if (icon) icon.innerHTML = isCollapsed ? FOLDER_OPEN_SVG : FOLDER_CLOSED_SVG;
    options.onToggle?.(folderDiv.querySelector('.tree-node')?.dataset.path, isCollapsed);
  };
  folderDiv.querySelector('.tree-chevron')?.addEventListener('click', toggle);
  folderDiv.querySelector('.tree-node')?.addEventListener('click', (e) => {
    if (!e.target.closest('.tree-file')) toggle(e);
  });
}

export function wireTreeFiles(fileNodes, onFileClick) {
  fileNodes.forEach(item => {
    item.addEventListener('click', () => onFileClick(item.dataset.path));
  });
}

export function renderTree(nodes, depth = 0) {
  const frag = document.createDocumentFragment();
  for (const node of nodes) {
    const type = node.kind === 'file' ? 'file' : 'folder';
    const div = renderTreeNode(node.name, node.path, type, depth);
    if (type === 'folder' && node.children?.length > 0) {
      const childrenDiv = div.querySelector('.tree-children');
      if (childrenDiv) childrenDiv.appendChild(renderTree(node.children, depth + 1));
    }
    frag.appendChild(div);
  }
  return frag;
}
