type WorkerBinding = {
  fetch(request: Request): Promise<Response>;
};

export interface Env {
  BACKEND: WorkerBinding;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    return env.BACKEND.fetch(request);
  },
};
