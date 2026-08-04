import type { ReactNode } from "react";

/** Auth shell: night-earth stage on the left, form rail on the right. */
export function LoginShell({ children }: { children: ReactNode }) {
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
