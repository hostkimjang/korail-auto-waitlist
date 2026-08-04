import { ApiError, request } from "./api/client";
import {
  awareTimestamp,
} from "./api/seatClasses";
import {
  filterTimetables,
  formTimeRange,
  mapTimetable,
  validateTravelDate,
} from "./api/timetables";

export { ApiError } from "./api/client";
export {
  getAuthStatus,
  loginWithPassword,
  logout,
  registerAdmin,
} from "./api/auth";
export {
  connectBrowserPush,
  createNotificationChannel,
  deleteNotificationChannel,
  disconnectBrowserPush,
  fetchNotificationChannels,
  readBrowserPushState,
  testNotificationChannel,
  updateNotificationChannel,
  waitForServiceWorkerRegistration,
} from "./api/notifications";
export { subscribeToEvents } from "./api/events";
export { fetchStations, mergeStationCatalogs } from "./api/stations";
export { normalizeSeatClasses } from "./api/seatClasses";
export { fetchTimetables, filterTimetables, mapTimetable } from "./api/timetables";
export {
  buildWatchCreatePayload,
  buildWatchCreatePayloads,
  cancelWatch,
  createWatch,
  deleteWatch,
  fetchWatches,
  mapWatch,
  pauseWatch,
  startWatch,
  updateWatch,
} from "./api/watches";

export const DEMO_MODE = import.meta.env.DEV && import.meta.env.VITE_DEMO_MODE !== "false";

const supportedProviders = new Set(["KORAIL", "SRT"]);

export async function refreshSeatStatus(form, providerOverride) {
  const provider = String(providerOverride ?? "").toUpperCase();
  if (!supportedProviders.has(provider)) {
    throw new ApiError("좌석 상태를 다시 조회할 운영사를 확인해 주세요.");
  }
  const { timeFrom, timeTo } = formTimeRange(form);
  validateTravelDate(form);
  const originNodeId = String(form.origin_node_id ?? "").trim();
  const destinationNodeId = String(form.destination_node_id ?? "").trim();
  if (!originNodeId || !destinationNodeId || originNodeId === destinationNodeId) {
    throw new ApiError("출발역과 도착역 식별자를 다시 선택해 주세요.");
  }
  const payload = await request("/seat-status/refresh", {
    method: "POST",
    body: JSON.stringify({
      provider: provider.toLowerCase(),
      origin: form.origin,
      destination: form.destination,
      departure_from: `${form.date}T${timeFrom}:00+09:00`,
      departure_to: `${form.date}T${timeTo}:00+09:00`,
      passenger_count: Number(form.passengers ?? form.passenger_count ?? 1),
      origin_node_id: originNodeId,
      destination_node_id: destinationNodeId,
    }),
  });
  return filterTimetables(form, payload).map(mapTimetable);
}

export async function fetchKorailSnapshotRevision(options = {}) {
  const payload = await request("/korail-browser-snapshot-revision", {
    method: "GET",
    cache: "no-store",
    signal: options.signal,
  });
  const revision = payload?.revision;
  return awareTimestamp(revision) ? revision : null;
}

export async function fetchBrowserCompanionStatus() {
  return request("/browser-companion/status", { cache: "no-store" });
}

export async function createBrowserCompanionPairing(label = "내 브라우저") {
  return request("/browser-companion/pairings", {
    method: "POST",
    body: JSON.stringify({ label }),
  });
}

export async function revokeBrowserCompanionCredential(id) {
  return request(`/browser-companion/credentials/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function fetchProviders() {
  return request("/providers");
}
