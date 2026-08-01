/** Mobile Drawer Manager — sliding panels with backdrop and swipe support. */

export class DrawerManager extends EventTarget {
  constructor() {
    super();
    this.activeDrawer = null;
    this.backdrop = document.getElementById('backdrop');

    this.backdrop.addEventListener('click', () => this.closeAll());

    document.querySelectorAll('[data-close]').forEach(btn => {
      btn.addEventListener('click', () => this.closeAll());
    });

    this.setupSwipe();
  }

  setupSwipe() {
    let startY = 0;
    let startX = 0;
    let startTime = 0;

    document.querySelectorAll('.drawer').forEach(drawer => {
      drawer.addEventListener('touchstart', (e) => {
        startY = e.touches[0].clientY;
        startX = e.touches[0].clientX;
        startTime = Date.now();
      }, { passive: true });

      drawer.addEventListener('touchend', (e) => {
        const endY = e.changedTouches[0].clientY;
        const endX = e.changedTouches[0].clientX;
        const deltaY = endY - startY;
        const deltaX = endX - startX;
        const deltaTime = Date.now() - startTime;

        // Only close on fast downward swipe with minimal horizontal drift
        if (deltaY > 50 && deltaTime < 300 && Math.abs(deltaX) < 30) {
          this.closeAll();
        }
      }, { passive: true });
    });
  }

  open(id) {
    if (this.activeDrawer && this.activeDrawer !== id) {
      this.close(this.activeDrawer, false);
    }

    const drawer = document.getElementById(`drawer-${id}`);
    if (!drawer) return;

    drawer.classList.add('open');
    this.backdrop.classList.add('open');
    this.activeDrawer = id;
    this.dispatchEvent(new CustomEvent('open', { detail: id }));
  }

  close(id, dispatch = true) {
    const drawer = document.getElementById(`drawer-${id}`);
    if (drawer) drawer.classList.remove('open');

    if (this.activeDrawer === id) {
      this.activeDrawer = null;
      this.backdrop.classList.remove('open');
      if (dispatch) {
        this.dispatchEvent(new CustomEvent('close', { detail: id }));
      }
    }
  }

  closeAll() {
    document.querySelectorAll('.drawer').forEach(d => d.classList.remove('open'));
    this.backdrop.classList.remove('open');
    const previous = this.activeDrawer;
    this.activeDrawer = null;
    if (previous) {
      this.dispatchEvent(new CustomEvent('close', { detail: previous }));
    }
  }

  toggle(id) {
    if (this.activeDrawer === id) {
      this.closeAll();
    } else {
      this.open(id);
    }
  }
}
