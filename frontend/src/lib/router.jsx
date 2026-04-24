import { useSyncExternalStore } from "react";

const NAVIGATION_EVENT = "app:navigate";

function isBrowser() {
  return typeof window !== "undefined";
}

function getPathname() {
  if (!isBrowser()) {
    return "/";
  }

  return window.location.pathname;
}

function subscribe(callback) {
  if (!isBrowser()) {
    return () => {};
  }

  const handleChange = () => callback();

  window.addEventListener("popstate", handleChange);
  window.addEventListener(NAVIGATION_EVENT, handleChange);

  return () => {
    window.removeEventListener("popstate", handleChange);
    window.removeEventListener(NAVIGATION_EVENT, handleChange);
  };
}

export function usePathname() {
  return useSyncExternalStore(subscribe, getPathname, () => "/");
}

export function navigate(to, { replace = false } = {}) {
  if (!isBrowser()) {
    return;
  }

  const nextUrl = new URL(to, window.location.origin);
  const nextLocation = `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`;
  const currentLocation = `${window.location.pathname}${window.location.search}${window.location.hash}`;

  if (nextLocation !== currentLocation) {
    window.history[replace ? "replaceState" : "pushState"](null, "", nextLocation);
  }

  window.dispatchEvent(new Event(NAVIGATION_EVENT));
}
