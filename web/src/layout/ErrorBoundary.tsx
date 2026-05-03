import React from "react";

type State = {
  hasError: boolean;
  message: string;
};

export class ErrorBoundary extends React.Component<React.PropsWithChildren, State> {
  state: State = { hasError: false, message: "" };

  static getDerivedStateFromError(error: unknown): State {
    return { hasError: true, message: String(error) };
  }

  componentDidCatch(error: unknown) {
    console.error("UI crashed:", error);
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div style={{ padding: 24, fontFamily: "Inter, Segoe UI, Arial, sans-serif" }}>
        <h2>页面发生错误</h2>
        <p>请刷新页面，若持续出现请联系运维并附上错误信息。</p>
        <pre>{this.state.message}</pre>
      </div>
    );
  }
}
