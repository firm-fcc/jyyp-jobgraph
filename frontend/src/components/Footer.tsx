/* 页脚 —— 设计稿的深色收口；色值归一到系统 --ink */

import { Link } from 'react-router-dom';

export function Footer() {
  return (
    <footer className="footer">
      {/* 页脚与正文之间原是一道直边：深色块自一条水平线起，切口硬。
          改为两层错开的波形收口 —— 后一层取极淡的同色，先在正文一侧铺一层
          过渡；前一层即页脚本身的边界。两层的波峰不对齐，边界因而有厚度。 */}
      <div className="footer-wave" aria-hidden="true">
        <svg viewBox="0 0 1440 90" preserveAspectRatio="none">
          <path
            className="fw-back"
            d="M0,90 L0,40 C200,4 380,72 620,58 C860,44 1010,0 1240,20 C1330,28 1390,38 1440,44 L1440,90 Z"
          />
          <path
            className="fw-front"
            d="M0,90 L0,58 C190,22 360,26 560,52 C760,78 900,84 1090,62 C1230,46 1350,34 1440,38 L1440,90 Z"
          />
        </svg>
      </div>
      <div className="footer-inner">
        <div className="footer-cols">
          <div className="footer-col footer-col-brand">
            <div className="footer-brand">
              <span className="dot" />
              JobGraph
            </div>
            <p>破解青年人才技能错配的</p>
            <p>动态技能图谱与可视化就业导航系统</p>
          </div>

          <div className="footer-col">
            <h4>功能</h4>
            <Link to="/panorama">全景图谱</Link>
            <Link to="/jobs">岗位洞察</Link>
            <Link to="/explore">职业探索</Link>
            <Link to="/match">人岗匹配</Link>
          </div>

          <div className="footer-col">
            <h4>数据来源</h4>
            <p>招聘信息</p>
            <p>学术论文</p>
            <p>行业新闻</p>
            <p className="footer-note">三类来源交叉验证，逐条回溯原文</p>
          </div>

          <div className="footer-col footer-col-about">
            <h4>关于</h4>
            <p>
              基于知识图谱与多源数据融合，构建岗位—任务—技能—技能点的四层映射体系，为青年人才提供精准的职业导航与技能提升路径。
            </p>
          </div>
        </div>

        <div className="footer-meta">
          <span>© 2026 JobGraph · 挑战杯参赛项目</span>
        </div>
      </div>
    </footer>
  );
}
