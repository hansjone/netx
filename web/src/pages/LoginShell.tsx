import type { ReactNode } from "react";

/** Auth shell modeled after CuteCloud login: space BG + rotating night-earth + centered form. */
export function LoginShell({ children }: { children: ReactNode }) {
  return (
    <div className="login-page">
      <div className="login-page__space" aria-hidden="true">
        <div className="login-planet">
          <div className="login-planet__railway" />
          <div className="login-planet__earth">
            <div className="login-planet__textures" />
          </div>
        </div>
      </div>
      <div className="login-page__content">{children}</div>
    </div>
  );
}
