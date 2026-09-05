/* ============================================================
   图内缩放控件

   点云类的图默认落在局部，此处提供退回整图与逐档缩放的入口。
   与 hooks/useZoomPan 配套，两张图（新岗位空间关系图、能力地形导航）共用。
   ============================================================ */

interface Props {
  k: number;
  maxK: number;
  onIn: () => void;
  onOut: () => void;
  onAll: () => void;
  /** 回到默认取景。为空则不显示该按钮 */
  onFocus?: () => void;
  /** 回到默认取景那一枚按钮的名称，如“回到选中”“回到路线” */
  focusLabel?: string;
}

export function ZoomBar({ k, maxK, onIn, onOut, onAll, onFocus, focusLabel }: Props) {
  const full = k <= 1.001;
  return (
    <div className="viz-zoom" role="group" aria-label="图内缩放">
      <button type="button" onClick={onOut} disabled={full} aria-label="缩小" title="缩小">
        −
      </button>
      <span className="viz-zoom-k" aria-live="off">
        {k.toFixed(1)}×
      </span>
      <button
        type="button"
        onClick={onIn}
        disabled={k >= maxK - 0.001}
        aria-label="放大"
        title="放大（也可双击图面，或按住 Ctrl 滚轮）"
      >
        ＋
      </button>
      <i className="viz-zoom-sep" />
      <button type="button" className="viz-zoom-t" onClick={onAll} disabled={full}>
        整图
      </button>
      {onFocus && (
        <button type="button" className="viz-zoom-t" onClick={onFocus}>
          {focusLabel ?? '回到默认'}
        </button>
      )}
    </div>
  );
}
