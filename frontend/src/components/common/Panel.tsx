import type { CSSProperties, ReactNode } from 'react';

interface Props {
  title: string;
  /** 面板副标题。目前不渲染 —— 各页的说明都已改到图内或图下的说明条里，
      标题栏再挂一段说明会把每张卡片的头部撑高一截。
      调用处仍然写着它，当作这块面板“要回答什么问题”的注释保留。 */
  sub?: string;
  actions?: ReactNode;
  children: ReactNode;
  bodyStyle?: CSSProperties;
  className?: string;
}

export function Panel({ title, actions, children, bodyStyle, className = '' }: Props) {
  return (
    <section className={`panel ${className}`}>
      <header className="panel-hd">
        <div className="panel-hd-text">
          <h2>{title}</h2>
        </div>
        {actions && <div className="panel-hd-act">{actions}</div>}
      </header>
      <div className="panel-bd" style={bodyStyle}>
        {children}
      </div>
    </section>
  );
}
