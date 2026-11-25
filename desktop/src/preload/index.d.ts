/// <reference types="vite/client" />

export interface CoreState {
  state: string;
  endpoint: string | null;
  restarts: number;
  lastError: string | null;
  pid: number | null;
}

export interface DafeiyuApi {
  readonly version: number;
  core(path: string, opts?: { method?: "GET" | "POST"; body?: string }): Promise<any>;
  app(method: string, params?: Record<string, unknown>): Promise<any>;
  info(): Promise<{ version: string; electron: string; mode: string; root: string; rendererBuilt: boolean }>;
  onCoreState(cb: (s: CoreState) => void): () => void;
}

declare global {
  interface Window {
    dafeiyu: DafeiyuApi;
  }
}

export {};
