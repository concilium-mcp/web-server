/* Mini barramento de eventos entre os módulos da dashboard.
   Quebra dependências circulares (views ↔ router, api ↔ main) sem framework. */

const listeners = new Map();

// registra um handler para um evento; devolve função para desregistrar
export function on(name, fn) {
  if (!listeners.has(name)) listeners.set(name, new Set());
  listeners.get(name).add(fn);
  return () => listeners.get(name)?.delete(fn);
}

export function emit(name, payload) {
  for (const fn of listeners.get(name) || []) fn(payload);
}
