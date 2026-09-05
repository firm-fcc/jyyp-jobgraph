/* ============================================================
   分析引擎 —— 页顶的对话式查询入口

   三件事：一排快速定位按钮把页面下方的各张图滚到眼前；一条对话流
   按名称查询图谱条目；回答后面跟着指向本页具体位置的链接，点一下
   就落到对应的图上，或把棱镜选中到那个条目。

   回答从图谱现算，同一个问题恒定给同一个结果，不依赖在线模型。
   接入真实模型时：resolve() 换成一次请求，下面的逐字输出换成读流，
   其余（气泡、定位链接、快速定位）都不用动 —— 这一层本来就只管呈现。

   之所以做成对话而不是搜索框：一次查询往往要接着追问，
   搜索框每问一次就把上一条冲掉，对不上前后文；对话流留着上一问，
   也让“回答里的链接”有地方待着，不至于问完就消失。
   ============================================================ */

import { useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '@/components/Icon';

/** 回答里的一个链接：跳到本页某张图，或选中图上的某个条目 */
export interface ChatLink {
  label: string;
  run: () => void;
}

export interface ChatAnswer {
  /** 一句话结论 */
  text: string;
  /** 跟在结论后面的定位链接 */
  links?: ChatLink[];
}

interface Message {
  id: number;
  role: 'user' | 'engine';
  text?: string;
  answer?: ChatAnswer;
}

interface Props {
  title: string;
  /** 标题下的一行说明。目前不渲染 —— 这一栏能答什么，快捷定位按钮和预设问题
      本身已经写在眼前，标题下再压一句说明只是把输入框往下推。
      调用处仍然写着它，当作这块面板“要回答什么问题”的注释保留。 */
  subtitle?: string;
  /** 快速定位按钮 */
  anchors: ChatLink[];
  /** 预设问题，点一下直接问 */
  suggestions: string[];
  resolve: (question: string) => ChatAnswer;
}

/** 逐字输出：每 16ms 吐 2 个字，一句结论约 0.8 秒写完 */
const STEP = 2;
const TICK = 16;
/** 提问到开始作答之间留一档停顿，“正在检索”这个状态才有地方落 */
const LATENCY = 360;
/** 输入框最多长到这个高度，再多就自己滚 */
const TA_MAX = 116;

export function EngineChat({ title, anchors, suggestions, resolve }: Props) {
  const [open, setOpen] = useState(true);
  const [q, setQ] = useState('');
  const [seq, setSeq] = useState(1);
  const [msgs, setMsgs] = useState<Message[]>([]);
  const [thinking, setThinking] = useState(false);
  /** 正在逐字输出的那条回答，以及已经写到第几个字 */
  const [typing, setTyping] = useState<{ id: number; shown: number } | null>(null);

  const bodyRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const latency = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(latency.current), []);

  /* 新消息、逐字输出、思考中都要把对话滚到底 */
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs, thinking, typing]);

  /* 逐字输出 */
  useEffect(() => {
    if (!typing) return;
    const full = msgs.find((m) => m.id === typing.id)?.answer?.text.length ?? 0;
    if (typing.shown >= full) {
      setTyping(null);
      return;
    }
    const t = window.setTimeout(
      () => setTyping((c) => (c ? { ...c, shown: Math.min(full, c.shown + STEP) } : c)),
      TICK,
    );
    return () => window.clearTimeout(t);
  }, [typing, msgs]);

  const grow = (el: HTMLTextAreaElement | null) => {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(TA_MAX, el.scrollHeight)}px`;
  };

  const ask = (question: string) => {
    const text = question.trim();
    if (!text || thinking) return;
    const uid = seq;
    const aid = seq + 1;
    setSeq((n) => n + 2);
    setMsgs((cur) => [...cur, { id: uid, role: 'user', text }]);
    setQ('');
    grow(taRef.current);
    setOpen(true);
    setThinking(true);
    latency.current = window.setTimeout(() => {
      setMsgs((cur) => [...cur, { id: aid, role: 'engine', answer: resolve(text) }]);
      setThinking(false);
      setTyping({ id: aid, shown: 0 });
    }, LATENCY);
  };

  /** 逐字输出中途点一下气泡就直接写完，不用干等 */
  const finish = () => setTyping(null);

  const reset = () => {
    window.clearTimeout(latency.current);
    setMsgs([]);
    setThinking(false);
    setTyping(null);
  };

  const sugs = useMemo(() => suggestions.slice(0, 4), [suggestions]);
  const avatar = (
    <span className="echat-ava" aria-hidden="true">
      <Icon name="spark" size={13} />
    </span>
  );

  return (
    <section className={open ? 'echat open' : 'echat'}>
      <header className="echat-hd">
        <span className="echat-orb" aria-hidden="true">
          <Icon name="spark" size={17} />
        </span>
        <div className="echat-hd-t">
          <b>{title}</b>
        </div>
        {open && msgs.length > 0 && (
          <button className="echat-clear" onClick={reset}>
            清空对话
          </button>
        )}
        <button
          className="echat-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label={open ? '收起分析引擎' : '展开分析引擎'}
        >
          {open ? '收起' : '展开'}
          <Icon name={open ? 'chevronU' : 'chevronD'} size={14} />
        </button>
      </header>

      {/* 收起走 CSS（.echat-panel 的 grid-template-rows 由 1fr 收到 0fr），这里不再挂 hidden 属性：
          [hidden] 的 display:none 来自浏览器默认样式表，优先级低于 .echat-panel 自己的 display，
          写了也不生效 —— 这正是之前点“收起”没有反应的原因。
          内层这一个 div 不能省：0fr 的行高只有在子元素 min-height:0 时才真的收得到 0。 */}
      <div className="echat-panel">
        <div className="echat-panel-inner">
          <div className="echat-anchors">
            <span className="echat-anchors-t">快捷定位</span>
            {anchors.map((a) => (
              <button key={a.label} className="echat-anchor" onClick={a.run}>
                <Icon name="arrowDown" size={12} />
                {a.label}
              </button>
            ))}
          </div>

          <div className="echat-body" ref={bodyRef}>
            {/* 空对话不留白板：先把这一栏能答什么、答案从哪来交代清楚 */}
            {msgs.length === 0 && !thinking && (
              <div className="echat-row engine">
                {avatar}
                <div className="echat-bubble">
                  <p>
                    可输入岗位、技能或技能点名称，亦可查询前瞻信号命中率、图谱的四层结构与规模。
                    回答由当前图谱现算，不调用在线模型；回答附带的按钮定位到对应的图，或在图上选中该条目。
                  </p>
                </div>
              </div>
            )}

            {msgs.map((m) =>
              m.role === 'user' ? (
                <div key={m.id} className="echat-row user">
                  <div className="echat-bubble user">{m.text}</div>
                </div>
              ) : (
                <div key={m.id} className="echat-row engine">
                  {avatar}
                  <div className="echat-bubble" onClick={finish}>
                    <p>
                      {typing?.id === m.id ? m.answer!.text.slice(0, typing.shown) : m.answer!.text}
                      {typing?.id === m.id && <i className="echat-caret" aria-hidden="true" />}
                    </p>
                    {typing?.id !== m.id && !!m.answer!.links?.length && (
                      <div className="echat-acts">
                        {m.answer!.links!.map((l) => (
                          <button key={l.label} className="echat-link" onClick={l.run}>
                            {l.label}
                            <Icon name="arrowR" size={12} />
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ),
            )}

            {thinking && (
              <div className="echat-row engine">
                {avatar}
                <div className="echat-bubble echat-dots" role="status" aria-label="正在检索图谱">
                  <i />
                  <i />
                  <i />
                </div>
              </div>
            )}
          </div>

          <form
            className="echat-form"
            onSubmit={(e) => {
              e.preventDefault();
              ask(q);
            }}
          >
            <div className="echat-sugs">
              {sugs.map((s) => (
                <button type="button" key={s} className="echat-sug" onClick={() => ask(s)}>
                  {s}
                </button>
              ))}
            </div>

            <div className="echat-input">
              <textarea
                ref={taRef}
                rows={1}
                value={q}
                placeholder="输入岗位、技能或技能点名称，或直接提问"
                aria-label="向分析引擎提问"
                onChange={(e) => {
                  setQ(e.target.value);
                  grow(e.target);
                }}
                /* 中文输入法选词时也会按回车，isComposing 期间必须放行 */
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    ask(q);
                  }
                }}
              />
              <button type="submit" className="echat-send" disabled={!q.trim() || thinking} aria-label="发送">
                <Icon name="send" size={16} />
              </button>
            </div>
          </form>
        </div>
      </div>
    </section>
  );
}
