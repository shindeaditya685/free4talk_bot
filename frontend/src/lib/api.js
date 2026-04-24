const configuredBackendUrl = import.meta.env.VITE_BACKEND_URL?.trim();

function resolveBackendOrigin() {
  if (configuredBackendUrl) {
    return configuredBackendUrl.replace(/\/+$/, "");
  }

  if (typeof window === "undefined") {
    return "";
  }

  const { hostname, origin, port, protocol } = window.location;
  const isLocalDevHost =
    hostname === "localhost" ||
    hostname === "127.0.0.1" ||
    hostname === "0.0.0.0";

  if (import.meta.env.DEV && isLocalDevHost && port === "5173") {
    return `${protocol}//${hostname}:8000`;
  }

  return origin;
}

export const BACKEND_ORIGIN = resolveBackendOrigin();

function withOrigin(pathname) {
  return BACKEND_ORIGIN ? `${BACKEND_ORIGIN}${pathname}` : pathname;
}

export const API_BASE = withOrigin("/api");

async function parseResponse(response) {
  const contentType = response.headers.get("content-type") ?? "";

  if (contentType.includes("application/json")) {
    return response.json();
  }

  const text = await response.text();
  return text ? { detail: text } : null;
}

async function request(path, { method = "GET", payload, headers, ...options } = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      Accept: "application/json",
      ...(payload !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: payload !== undefined ? JSON.stringify(payload) : undefined,
    ...options,
  });

  const data = await parseResponse(response);

  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? data.detail
        : `Request failed with status ${response.status}`;
    const error = new Error(detail);

    error.response = {
      data: data && typeof data === "object" ? data : { detail },
    };
    error.status = response.status;

    throw error;
  }

  return data;
}

function wrapData(promise) {
  return promise.then((data) => ({ data }));
}

export const api = {
  get: (path, options) => wrapData(request(path, { method: "GET", ...options })),
  post: (path, payload, options) =>
    wrapData(request(path, { method: "POST", payload, ...options })),
  patch: (path, payload, options) =>
    wrapData(request(path, { method: "PATCH", payload, ...options })),
  delete: (path, options) =>
    wrapData(request(path, { method: "DELETE", ...options })),
};

export const listBots = () => api.get("/bots").then((r) => r.data);
export const createBot = (payload) =>
  api.post("/bots", payload).then((r) => r.data);
export const deleteBot = (id) => api.delete(`/bots/${id}`).then((r) => r.data);
export const startBot = (id) =>
  api.post(`/bots/${id}/start`).then((r) => r.data);
export const stopBot = (id) => api.post(`/bots/${id}/stop`).then((r) => r.data);
export const getStatus = (id) =>
  api.get(`/bots/${id}/status`).then((r) => r.data);
export const updateBot = (id, payload) =>
  api.patch(`/bots/${id}`, payload).then((r) => r.data);

export const viewerUrl = (id) => withOrigin(`/api/bots/${id}/viewer`);
