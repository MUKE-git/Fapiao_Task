"""Presentation-only homepage and shared navigation for the Streamlit app."""
from base64 import b64encode
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).resolve().parent / "01 docs" / "assets"


def _image(name: str) -> str:
    return "data:image/png;base64," + b64encode((ASSETS / name).read_bytes()).decode("ascii")


def render_navigation() -> None:
    st.html("""<style>
    :root {color-scheme:light}
    .stApp {background:#f8f8f6;color:#202020}
    [data-testid="stHeader"] {display:none}
    .block-container {max-width:1320px;padding:1.25rem 2.5rem 2rem}
    .site-nav {display:flex;align-items:center;justify-content:space-between;padding:0 0 16px;border-bottom:1px solid #deded9;gap:20px}
    .site-brand {display:flex;align-items:center;gap:10px;font-size:17px;font-weight:650;letter-spacing:.03em}
    .brand-mark {display:grid;place-items:center;background:#232323;color:white;width:32px;height:36px;border-radius:7px;font-size:19px}
    .site-nav a {text-decoration:none;color:#555;font-size:16px}
    .site-links {display:flex;align-items:center;gap:26px}
    .site-links .nav-use {padding:10px 18px;background:#222;color:#fff;border-radius:6px}
    .eyebrow {font-size:12px;letter-spacing:.12em;color:#777;margin-bottom:12px;font-weight:600}
    .home-hero {display:grid;grid-template-columns:1fr 1fr;gap:38px;align-items:center;padding:36px 0 40px}
    .home-hero h1 {font-size:clamp(36px,4vw,56px);font-weight:650;line-height:1.22;letter-spacing:-.045em;margin:0 0 18px;color:#202020}
    .home-lead {font-size:20px;line-height:1.55;color:#575752;max-width:490px;margin:0 0 22px}
    .home-actions {display:flex;gap:10px;flex-wrap:wrap}
    .home-actions a {display:inline-flex;align-items:center;justify-content:center;gap:18px;text-decoration:none;font-size:16px;padding:11px 18px;border:1px solid #cececa;border-radius:6px;color:#292929;background:transparent}
    .home-actions .primary {background:#242424;color:#fff;border-color:#242424}
    .home-actions a:hover,.site-links a:hover {opacity:.65}
    .hero-note {margin-top:18px;font-size:14px;letter-spacing:.04em;color:#8b8b85}
    .preview-wrap {position:relative;background:#eaeae6;border:1px solid #deded8;border-radius:12px;padding:18px;box-shadow:0 24px 60px #2020200b}
    .preview-bar {display:flex;gap:5px;align-items:center;margin-bottom:12px;color:#777;font-size:12px;letter-spacing:.05em}
    .preview-bar i {width:6px;height:6px;background:#bcbcb5;border-radius:50%}
    .preview-bar span {margin-left:auto}
    .preview-wrap img {width:100%;display:block;border:1px solid #ddd;border-radius:7px}
    .preview-caption {font-size:14px;color:#707068;margin-top:10px;text-align:center}
    .home-section {border-top:1px solid #deded9;padding:34px 0 38px;scroll-margin-top:18px}
    .section-head {display:flex;justify-content:space-between;align-items:end;gap:24px;margin-bottom:20px}
    .section-head h2 {font-size:34px;font-weight:600;letter-spacing:-.04em;margin:0;color:#242424}
    .section-head p {font-size:16px;color:#65655f;line-height:1.5;max-width:470px;margin:0}
    .result-preview {background:#eeeee9;border:1px solid #deded7;border-radius:10px;padding:16px;margin-bottom:0;overflow:auto}
    .result-preview img {display:block;width:100%;min-width:680px;border-radius:4px}
    .result-label {display:flex;justify-content:space-between;font-size:14px;color:#65655f;margin-bottom:12px}
    .steps {display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
    .step h3 {font-size:19px;font-weight:600;margin:0 0 10px;color:#292929;line-height:1.4}
    .step p {font-size:16px;color:#63635d;line-height:1.55;margin:0}
    .delivery-layout {display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px;align-items:stretch}
    .package-panel,.pdf-panel {padding:22px;border:1px solid #d9d9d2;border-radius:10px;background:#fdfdfb;min-width:0}
    .pdf-panel {background:#eeeeea}
    .panel-heading h3 {font-size:21px;line-height:1.35;font-weight:600;margin:6px 0 16px;letter-spacing:-.02em}
    .output-index {font-size:12px;color:#77776f;letter-spacing:.1em}
    .archive-name {display:flex;align-items:center;gap:12px;border-bottom:1px solid #deded7;padding:0 0 16px}
    .archive-name strong {font-size:16px;font-weight:600;overflow-wrap:anywhere}
    .archive-name div>span {display:block;font-size:14px;color:#77776f;margin-top:5px;line-height:1.4}
    .archive-icon,.pdf-icon {display:grid;place-items:center;flex-shrink:0;width:40px;height:48px;border:1px solid #bfbfb6;border-radius:4px;background:#f7f7f3;font-size:12px;font-weight:600;letter-spacing:.03em;color:#55554d}
    .archive-icon {background:#282828;color:#fff;border-color:#282828}
    .folder-tree {font-size:15px;line-height:1.4;margin-top:16px;color:#51514b;overflow-wrap:anywhere}
    .tree-root {font-weight:600;color:#242424}
    .folder-tree ul {list-style:none;margin:7px 0 0 8px;padding-left:18px;border-left:1px solid #d3d3c9}
    .folder-tree li {position:relative;margin:0;padding:5px 0}
    .folder-tree li:before {content:"";position:absolute;width:11px;height:1px;background:#d3d3c9;left:-18px;top:15px}
    .tree-type {font-size:10px;display:inline-block;width:35px;color:#88887d}
    .panel-note {font-size:14px;line-height:1.5;color:#717169;margin:16px 0 0}
    .panel-intro {font-size:16px;line-height:1.5;margin:0;color:#65655d}
    .naming-rule {display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:16px 0}
    .naming-rule span {font-size:14px;padding:6px 10px;border:1px solid #cecec5;border-radius:4px;background:#f8f8f5}
    .naming-rule b {font-size:14px;color:#77776c;font-weight:400}
    .pdf-file {display:flex;align-items:center;gap:12px;background:#fbfbf9;border:1px solid #d9d9d0;border-radius:7px;padding:14px 12px;margin-top:10px;min-width:0}
    .pdf-file div {min-width:0}.pdf-file strong {display:block;font-size:16px;font-weight:550;line-height:1.4;overflow-wrap:anywhere;color:#292923}
    .pdf-file div>span {display:block;font-size:14px;line-height:1.4;color:#77776d;margin-top:6px}
    .excel-panel {border:1px solid #d9d9d2;border-radius:10px;background:#fdfdfb;padding:20px}
    .excel-heading {display:flex;align-items:center;justify-content:space-between;gap:24px;margin-bottom:12px}
    .excel-heading h3 {margin-bottom:0}.excel-heading p {font-size:16px;line-height:1.5;color:#66665e;max-width:400px;margin:0}
    .stApp [data-testid="stWidgetLabel"] p,.stApp [data-testid="stCheckbox"] p {font-size:16px;line-height:1.4}
    .stApp [data-testid="stTextInput"] input,.stApp [data-baseweb="select"] {font-size:16px}
    .stApp [data-testid="stVerticalBlock"] {gap:.8rem}
    .stApp [data-testid="stForm"] {max-width:840px}
    .result-foot {font-size:14px;line-height:1.5;color:#707068;margin:14px 0 0}
    .step {padding:16px 18px 12px 0;border-top:1px solid #c9c9c1}
    .step-number {display:block;font-size:30px;font-weight:400;letter-spacing:-.05em;color:#99998f;margin-bottom:12px}
    .guide-cta {display:flex;justify-content:space-between;align-items:center;gap:20px;margin-top:18px;padding:18px 22px;background:#edede8;border-radius:8px}
    .guide-cta p {font-size:17px;color:#5c5c56;margin:0}
    .site-footer {display:flex;justify-content:space-between;gap:20px;border-top:1px solid #deded9;padding-top:16px;color:#77776f;font-size:14px;line-height:1.5}
    .use-heading {margin:24px 0 18px}
    .use-heading h1 {font-size:36px;font-weight:600;letter-spacing:-.04em;margin-bottom:12px}
    .use-heading p {font-size:17px;color:#666}
    [data-testid="stForm"] {background:#fff;border-color:#deded9;border-radius:10px;padding:20px}
    .stApp button[kind="secondaryFormSubmit"] {background:#222;color:#fff;border-color:#222}
    @media(max-width:800px) {
      .block-container {padding:1rem 1.2rem 1.5rem}
      .site-nav {flex-direction:column;align-items:stretch;gap:14px}
      .site-links {gap:15px;justify-content:space-between}.site-brand {font-size:16px;white-space:nowrap}.site-links a {font-size:15px;white-space:nowrap}
      .home-hero {grid-template-columns:1fr;gap:22px;padding:26px 0}
      .home-hero h1 {font-size:36px}.home-lead {font-size:18px;max-width:none}
      .preview-wrap {transform:none;max-width:520px;width:100%;box-sizing:border-box;margin:auto}
      .home-section {padding:28px 0}.section-head {display:block}.section-head p {margin-top:10px}
      .delivery-layout,.steps {grid-template-columns:1fr}.step-number {margin-bottom:12px}
      .package-panel,.pdf-panel,.excel-panel {padding:16px}
      .excel-heading {display:block}.excel-heading p {margin-top:10px}
      .folder-tree {font-size:14px}.pdf-file strong {font-size:15px}
      .guide-cta {align-items:start;flex-direction:column}.site-footer {flex-direction:column;gap:8px}
    }
    @media(prefers-reduced-motion:no-preference) {html {scroll-behavior:smooth}}
    </style>
    <nav class="site-nav" aria-label="主导航">
      <a class="site-brand" href="?page=home" target="_self"><span class="brand-mark">▤</span>发票邮件处理助手</a>
      <div class="site-links"><a href="?page=home" target="_self">首页</a><a href="?page=home#results" target="_self">效果</a><a href="?page=home#guide" target="_self">指南</a><a class="nav-use" href="?page=use" target="_self">开始使用 ↗</a></div>
    </nav>""")


def render_home() -> None:
    form_image = _image("form-preview.png")
    summary_image = _image("summary-preview.png")
    st.html(f"""
    <main>
      <section class="home-hero" id="home">
        <div><div class="eyebrow">INVOICE MAIL ASSISTANT / 发票整理工具</div>
          <h1>发票邮件处理助手</h1>
          <p class="home-lead">一个处理发票邮件的助手<br>帮你下载附件，填报销表，提取信息，重命名文件</p>
          <div class="home-actions"><a class="primary" href="?page=use" target="_self">开始使用 <span>↗</span></a><a href="#results" target="_self">查看效果 ↓</a><a href="#guide" target="_self">使用指南 ↓</a></div>
          <p class="hero-note">从收件箱到报销材料，让整理更有条理。</p>
        </div>
        <div class="preview-wrap"><div class="preview-bar"><i></i><i></i><i></i><span>WORKSPACE / 使用页</span></div>
          <img src="{form_image}" alt="使用页真实表单截图：选择 IMAP 服务器、填写邮箱账号及授权码，然后开始处理">
          <div class="preview-caption">选择邮箱 · 填写授权码 · 开始处理</div>
        </div>
      </section>
      <section class="home-section" id="results">
        <div class="eyebrow">01 / THE OUTPUT</div>
        <div class="section-head"><h2>你会拿到什么</h2><p>一次处理，一份打包好的报销材料。<br>完成后，点击「下载本轮结果」获取 ZIP。</p></div>
        <div class="delivery-layout">
          <article class="package-panel">
            <div class="panel-heading"><span class="output-index">01 / ZIP</span><h3>下载后，文件都在这里</h3></div>
            <div class="archive-name"><span class="archive-icon">ZIP</span><div><strong>fapiao_YYYYMMDD_HHMMSS.zip</strong><span>一次下载，包含汇总表、处理记录与票据</span></div></div>
            <div class="folder-tree" aria-label="压缩包目录结构示例">
              <div class="tree-root">Invoice_Task/</div>
              <ul>
                <li><span class="tree-type">XLSX</span>发票信息汇总表.xlsx</li>
                <li><span class="tree-type">JSON</span>task_debug.json</li>
                <li><strong>用户票据/</strong><ul>
                  <li>客运服务费+2026-09-12+86.52元.pdf</li>
                  <li>行程单+2026-09-12+86.52元.pdf</li>
                  <li>代收通行费+2026-09-17+12.6元.pdf</li>
                  <li>行程单+2026-09-17+12.6元.pdf</li>
                  <li>客运服务费+2026-09-23+112.35元.pdf</li>
                  <li>行程单+2026-09-23+112.35元.pdf</li>
                  <li>配送服务费+2026-09-22+35.8元.pdf</li>
                </ul></li>
              </ul>
            </div>
            <p class="panel-note">task_debug.json 保留来源邮件、文件和处理原因，方便回查待核事项。</p>
          </article>
          <article class="pdf-panel">
            <div class="panel-heading"><span class="output-index">02 / PDF</span><h3>票据已按业务、日期、金额命名</h3></div>
            <p class="panel-intro">不用逐个打开文件，先从文件名找到需要的票据。</p>
            <div class="naming-rule"><span>业务名</span><b>+</b><span>日期</span><b>+</b><span>金额</span><b>.pdf</b></div>
            <div class="pdf-file"><span class="pdf-icon">PDF</span><div><strong>客运服务费+2026-09-12+86.52元.pdf</strong><span>发票 · 客运服务费 / 2026-09-12 / 86.52 元</span></div></div>
            <div class="pdf-file"><span class="pdf-icon">PDF</span><div><strong>行程单+2026-09-12+86.52元.pdf</strong><span>对应行程单 · 与上方发票配对整理</span></div></div>
            <div class="pdf-file"><span class="pdf-icon">PDF</span><div><strong>配送服务费+2026-09-22+35.8元.pdf</strong><span>发票 · 配送服务费 / 2026-09-22 / 35.8 元</span></div></div>
            <p class="panel-note">如有其他命名需要，可联系开发者定制<br>有可用的原始 PDF 时优先交付其副本；符合条件的图片可转成 PDF。</p>
          </article>
        </div>
        <article class="excel-panel">
          <div class="excel-heading"><div class="panel-heading"><span class="output-index">03 / XLSX</span><h3>报销字段，汇总到一张表</h3></div><p>每张发票一条记录；已匹配的交通类发票可汇入行程信息。</p></div>
          <div class="result-preview"><div class="result-label"><span>发票信息汇总表.xlsx</span><span>八列报销字段 / 示例</span></div><img src="{summary_image}" alt="README 中的发票信息汇总表示例，包含发票日期、发票号码、报销金额、发票类型、票面类型、出行时间、销售方及出发地/目的地" loading="lazy"></div>
        </article>
        <p class="result-foot">以上目录、文件名及表格均为 README 示例，实际票据数量取决于本轮可交付的文件。字段缺失、冲突或配对不确定时，请按提示核对原件。</p>
      </section>
      <section class="home-section" id="guide">
        <div class="eyebrow">02 / QUICK START</div>
        <div class="section-head"><h2>三步，开始整理</h2><p>准备好邮箱和目标邮件，<br>接下来的整理交给助手。</p></div>
        <div class="steps">
          <article class="step"><span class="step-number">01</span><h3>邮箱打开 imap 服务，保存 imap 码</h3><p>在邮箱设置中开启 IMAP 服务，保存邮箱生成的授权码，稍后填写表单时使用。</p></article>
          <article class="step"><span class="step-number">02</span><h3>需要处理的发票邮件标为未读</h3><p>将目标邮件放在收件箱并标为未读。助手会筛选主题符合发票相关关键词的未读邮件。</p></article>
          <article class="step"><span class="step-number">03</span><h3>网页填表单提交</h3><p>选择邮箱服务器，填写邮箱账号和授权码，点击「开始处理」。完成后查看待核提示，下载本轮结果。</p></article>
        </div>
        <div class="guide-cta"><p>准备好了？从你的收件箱开始。</p><div class="home-actions"><a class="primary" href="?page=use" target="_self">前往使用页 ↗</a></div></div>
        <p class="result-foot">程序不会自动标记邮件已读，同一批未读邮件再次运行可能重复输出。材料整理完成后，真伪查验和报销审批仍需按原有流程进行。</p>
      </section>
    </main>
    <footer class="site-footer"><span>▤ 发票邮件处理助手</span><span>邮件归集 / 字段汇总 / 票据整理</span></footer>
    """)
