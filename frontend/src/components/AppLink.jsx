import { navigate } from "../lib/router";

export function Link({ to, onClick, target, ...props }) {
  const href = typeof to === "string" ? to : String(to);

  const handleClick = (event) => {
    onClick?.(event);

    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.altKey ||
      event.ctrlKey ||
      event.shiftKey ||
      (target && target !== "_self")
    ) {
      return;
    }

    const nextUrl = new URL(href, window.location.origin);

    if (nextUrl.origin !== window.location.origin) {
      return;
    }

    event.preventDefault();
    navigate(`${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`);
  };

  return <a href={href} onClick={handleClick} target={target} {...props} />;
}
