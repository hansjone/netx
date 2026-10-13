import { useEffect, type ReactNode } from "react";
import { LoginStarfield } from "./LoginStarfield";

/** Auth shell: night earth adrift in deep space on the left, form rail on the right. */
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
        <LoginStarfield />
        <div className="login-planet">
          <div className="login-planet__earth">
            <div className="login-planet__textures" />
          </div>
        </div>
      </div>
      <aside className="login-page__rail">{children}</aside>
    </div>
  );
}
