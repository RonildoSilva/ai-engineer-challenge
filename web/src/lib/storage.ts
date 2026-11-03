// localStorage é conveniência (sessão e tema): numa aba privada ou com storage bloqueado, a página
// continua funcionando, só não lembra entre recarregamentos.
export const storage = {
  get(key: string): string | null {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key: string, value: string): void {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      /* sem storage */
    }
  },
  remove(key: string): void {
    try {
      window.localStorage.removeItem(key);
    } catch {
      /* sem storage */
    }
  },
};
