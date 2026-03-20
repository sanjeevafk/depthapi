type WorkerBinding = {
  fetch(request: Request): Promise<Response>;
};

export interface Env {
  LLM: WorkerBinding;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    return env.LLM.fetch(request);
  },
};
