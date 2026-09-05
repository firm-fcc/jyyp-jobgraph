/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  /** 'real' 时词表种子取 Reference/data 下算法侧产出的真实分类，其余取演示词表 */
  readonly VITE_DATA?: string;
  /** 人岗匹配后端（backend/）的地址，如 http://127.0.0.1:8000。未配置时本页回落演示链路 */
  readonly VITE_MATCH_API?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
