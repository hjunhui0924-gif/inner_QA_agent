<script setup lang="ts">
import { ref } from 'vue'
import {
  ArrowRight,
  Document,
  Connection,
  Search,
  Check,
} from '@element-plus/icons-vue'
import '../styles/home.css'
const sourceVisible = ref(false)
const scenarios = [
  {
    icon: Search,
    title: '查制度',
    text: '差旅费用需要谁审批？',
    detail: '从企业资料中查找适用规则，让答案有出处。',
  },
  {
    icon: Connection,
    title: '理流程',
    text: '申请之前，要准备什么？',
    detail: '把分散的信息整理成易读的步骤，接着追问。',
  },
  {
    icon: Document,
    title: '核对依据',
    text: '这条结论，原文怎么说？',
    detail: '按需展开引用，查看文档信息或下载已有来源。',
  },
]
</script>

<template>
  <div class="product-home">
    <a href="#home-intro" class="skip-link">跳转到主要内容</a>
    <header class="home-nav">
      <RouterLink to="/" class="home-brand"
        ><img
          src="/knowledge-assistant.png"
          width="30"
          height="30"
          alt=""
        />内部知识助手</RouterLink
      >
      <nav aria-label="首页导航">
        <a href="#capabilities">产品能力</a
        ><RouterLink to="/chat" class="primary-button"
          >进入工作台 <el-icon aria-hidden="true"><ArrowRight /></el-icon
        ></RouterLink>
      </nav>
    </header>

    <section id="home-intro" class="home-hero">
      <div class="hero-copy">
        <p class="home-kicker"><span /> 企业知识 · 随问随查</p>
        <h1>
          让企业知识，<br />成为<span>有据可查</span
          ><br class="hero-break" />的答案。
        </h1>
        <p class="hero-description">
          从制度、流程到业务资料，把工作中的问题交给知识助手。读懂答案，也看得见依据。
        </p>
        <div class="hero-actions">
          <RouterLink to="/chat" class="primary-button"
            >进入问答工作台
            <el-icon aria-hidden="true"><ArrowRight /></el-icon></RouterLink
          ><a href="#how-it-works" class="home-secondary"
            >了解使用方式 <span aria-hidden="true">↗</span></a
          >
        </div>
        <p class="hero-note">企业资料问答 / 原文引用 / 连续追问</p>
      </div>

      <div class="home-preview" aria-label="示例问答，使用合成资料">
        <div class="preview-top">
          <span
            ><img
              src="/knowledge-assistant.png"
              width="19"
              height="19"
              alt=""
            />
            问答工作台</span
          ><span class="sample-label">示例问答</span>
        </div>
        <div class="preview-conversation">
          <div class="preview-question">差旅报销超过 5000 元，需要谁审批？</div>
          <div class="preview-answer">
            <span class="preview-author"
              ><el-icon aria-hidden="true"><Document /></el-icon> 知识助手</span
            >
            <p>根据示例差旅制度，单笔差旅报销超过 5000 元时：</p>
            <ol>
              <li>先由<strong>直属主管审批</strong>。</li>
              <li>
                再由<strong>财务负责人复核</strong>。<sup class="sample-citation"><button
                  type="button"
                  aria-label="查看示例参考来源 1"
                  :aria-expanded="sourceVisible"
                  aria-controls="sample-source"
                  @click="sourceVisible = !sourceVisible"
                >[1]</button></sup>
              </li>
            </ol>
          </div>
          <button
            class="preview-source-button"
            type="button"
            :aria-expanded="sourceVisible"
            aria-controls="sample-source"
            @click="sourceVisible = !sourceVisible"
          >
            <el-icon aria-hidden="true"><Document /></el-icon
            >{{ sourceVisible ? '收起示例来源' : '查看示例来源'
            }}<span>1 条引用</span>
          </button>
          <div
            id="sample-source"
            class="sample-source"
            :class="{ expanded: sourceVisible }"
          >
            <template v-if="sourceVisible"
              ><strong>示例差旅制度 · 审批规则</strong>
              <blockquote>
                单笔差旅报销金额超过 5000
                元的，由直属主管审批后，再由财务负责人复核。
              </blockquote></template
            >
            <p v-else>
              <el-icon aria-hidden="true"><Check /></el-icon>
              点击来源，核对回答中的每一项依据。
            </p>
          </div>
        </div>
        <div class="preview-bottom">
          以上为合成资料演示，不代表贵公司的实际制度。
        </div>
      </div>
    </section>

    <section id="capabilities" class="home-capabilities">
      <div class="home-section-title">
        <p class="home-kicker">从问题出发</p>
        <h2>少一点翻找，多一点确定。</h2>
      </div>
      <div class="scenario-grid">
        <article v-for="scenario in scenarios" :key="scenario.title">
          <el-icon aria-hidden="true"
            ><component :is="scenario.icon"
          /></el-icon>
          <h3>{{ scenario.title }}</h3>
          <strong>{{ scenario.text }}</strong>
          <p>{{ scenario.detail }}</p>
        </article>
      </div>
    </section>

    <section id="how-it-works" class="home-how">
      <div>
        <p class="home-kicker">融入日常工作</p>
        <h2>让资料用起来，<br />只需三步。</h2>
        <RouterLink to="/knowledge" class="home-secondary"
          >维护资料？进入知识管理 <span aria-hidden="true">↗</span></RouterLink
        >
      </div>
      <ol>
        <li>
          <span>01</span>
          <div>
            <h3>添加资料</h3>
            <p>由知识维护人员上传文档，填写文档信息与生效设置。</p>
          </div>
        </li>
        <li>
          <span>02</span>
          <div>
            <h3>直接提问</h3>
            <p>在工作台用自然语言描述问题，按需继续追问。</p>
          </div>
        </li>
        <li>
          <span>03</span>
          <div>
            <h3>阅读答案与来源</h3>
            <p>先读回答，再展开原文引用核对关键条件。</p>
          </div>
        </li>
      </ol>
    </section>

    <footer class="home-footer">
      <div>
        <h2>从下一个工作问题开始。</h2>
        <p>重要事项，请以原始资料为准。</p>
      </div>
      <RouterLink to="/chat" class="primary-button"
        >进入问答工作台
        <el-icon aria-hidden="true"><ArrowRight /></el-icon></RouterLink
      ><small>内部知识助手 · 企业知识工作台</small>
    </footer>
  </div>
</template>
