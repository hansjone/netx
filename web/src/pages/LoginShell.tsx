import { useEffect, type ReactNode } from "react";

/** Auth shell: night-earth stage on the left, form rail on the right. */
export function LoginShell({ children }: { children: ReactNode }) {
  // Pause ambient CSS loops while the tab is hidden.
  useEffect(() => {
    const root = document.documentElement;
    const sync = () => {
      root.classList.toggle("login-page--paused", document.hidden);
    };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      root.classList.remove("login-page--paused");
    };
  }, []);

  return (
    <div className="login-page">
      <div className="login-page__space" aria-hidden="true">
        <div className="login-planet">
          <div className="login-planet__earth">
            <div className="login-planet__textures" />
          </div>
        </div>
      </div>
      {/* Sibling of space so blooms sit above the stage, under the login card. */}
      <div className="login-page__flares" aria-hidden="true">
        <span className="login-page__flare login-page__flare--a" />
      </div>
      <aside className="login-page__rail">{children}</aside>
    </div>
  );
}
