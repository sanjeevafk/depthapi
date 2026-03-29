import "@testing-library/jest-dom/vitest";

class ResizeObserver {
  private callback: ResizeObserverCallback;
  private targets = new Set<Element>();

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }

  observe(target: Element) {
    this.targets.add(target);
    const entry = { target, contentRect: target.getBoundingClientRect() } as ResizeObserverEntry;
    this.callback([entry], this);
  }

  unobserve(target: Element) {
    this.targets.delete(target);
  }

  disconnect() {
    this.targets.clear();
  }

  trigger(target?: Element) {
    const targets = target ? [target] : Array.from(this.targets);
    const entries = targets.map(
      (entryTarget) =>
        ({ target: entryTarget, contentRect: entryTarget.getBoundingClientRect() }) as ResizeObserverEntry,
    );
    if (entries.length) {
      this.callback(entries, this);
    }
  }
}

if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = ResizeObserver;
}
