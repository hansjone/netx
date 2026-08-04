import type { ReactNode } from "react";

/** Auth shell: night-earth stage on the left, form rail on the right. */
export function LoginShell({ children }: { children: ReactNode }) {
  return (
    <div className="login-page">
      <div className="login-page__space" aria-hidden="true">
        <div className="login-page__sun" />
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
