import { useEffect, useRef, useState } from "react";

interface TurnstileApi {
  render: (
    container: HTMLElement,
    options: {
      sitekey: string;
      theme: "auto";
      callback: (token: string) => void;
      "expired-callback": () => void;
      "error-callback": () => void;
    },
  ) => string;
  remove: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

const scriptId = "cloudflare-turnstile-script";
const scriptSource =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  return new Promise((resolve, reject) => {
    let script = document.getElementById(scriptId) as HTMLScriptElement | null;
    const loaded = () => {
      if (window.turnstile) resolve(window.turnstile);
      else reject(new Error("Turnstile did not initialize"));
    };
    const failed = () => {
      script?.remove();
      reject(new Error("Turnstile could not be loaded"));
    };
    if (!script) {
      script = document.createElement("script");
      script.id = scriptId;
      script.src = scriptSource;
      script.async = true;
      script.defer = true;
      document.head.append(script);
    }
    script.addEventListener("load", loaded, { once: true });
    script.addEventListener("error", failed, { once: true });
  });
}

export function TurnstileWidget({
  siteKey,
  onTokenChange,
}: {
  siteKey: string;
  onTokenChange: (token: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let widgetId = "";
    setLoadError(false);
    onTokenChange("");
    void loadTurnstile()
      .then((turnstile) => {
        if (cancelled || !containerRef.current) return;
        widgetId = turnstile.render(containerRef.current, {
          sitekey: siteKey,
          theme: "auto",
          callback: onTokenChange,
          "expired-callback": () => onTokenChange(""),
          "error-callback": () => onTokenChange(""),
        });
      })
      .catch(() => {
        if (!cancelled) {
          onTokenChange("");
          setLoadError(true);
        }
      });
    return () => {
      cancelled = true;
      if (widgetId && window.turnstile) window.turnstile.remove(widgetId);
    };
  }, [onTokenChange, siteKey]);

  return (
    <div className="turnstile-frame">
      <div aria-label="人机验证" ref={containerRef} />
      <small>
        {loadError
          ? "人机验证组件加载失败，请刷新页面后重试。"
          : "注册前请完成人机验证。"}
      </small>
    </div>
  );
}
