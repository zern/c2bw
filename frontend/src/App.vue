<template>
  <div
    class="app-container"
    @dragover="handleDragOver"
    @dragleave="handleDragLeave"
    @drop="handleDrop"
  >
    <!-- 拖拽提示遮罩 -->
    <div v-if="isDragging" class="drag-drop-overlay">
      <div class="drag-drop-box">
        <el-icon><UploadFilled /></el-icon>
        <div class="drag-drop-title">{{ t('dragDropTitle') }}</div>
        <div class="drag-drop-subtitle">{{ t('dragDropSubtitle') }}</div>
      </div>
    </div>

    <!-- 顶部导航栏 -->
    <header class="app-header">
      <div class="brand-mark"><el-icon><Reading /></el-icon></div>
      <div class="brand-copy">
        <h1>{{ t('appTitle') }}</h1>
        <p>{{ t('appSubtitle') }}</p>
      </div>
      <div class="header-right">
        <el-tag class="version-tag" size="small" effect="plain">v3.6</el-tag>
        
        <!-- 语言选择 -->
        <el-dropdown trigger="click" @command="changeLanguage" class="lang-dropdown">
          <span class="lang-trigger" :title="t('langSelectTip')">
            <span class="lang-icon">🌐</span> {{ currentLanguageLabel }} <el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu class="lang-dropdown-menu">
              <el-dropdown-item
                v-for="l in supportedLanguages"
                :key="l.code"
                :command="l.code"
                :class="{ 'is-active-lang': currentLang === l.code }"
              >
                <el-icon v-if="currentLang === l.code" style="margin-right: 4px;"><Check /></el-icon>
                <span v-else style="display:inline-block; width:18px;"></span>
                {{ l.label }}
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>

        <!-- 更新提示 -->
        <el-popover v-if="updateAvailable" placement="bottom-end" :width="300" trigger="hover">
          <template #reference>
            <el-badge is-dot class="update-badge"><el-icon class="update-icon"><Bell /></el-icon></el-badge>
          </template>
          <div class="update-popover">
            <div class="update-title">{{ t('updateFound') }} {{ updateInfo.version }}</div>
            <div v-if="updateInfo.release_date" class="update-date">{{ t('releaseDate') }}{{ updateInfo.release_date }}</div>
            <ul class="update-list">
              <li v-for="(item, index) in updateInfo.changes" :key="index">{{ item }}</li>
            </ul>
            <el-button
              type="primary"
              size="small"
              :icon="Download"
              :disabled="!updateInfo.download_url"
              @click="downloadUpdate"
            >{{ t('downloadUpdate') }}</el-button>
          </div>
        </el-popover>
      </div>
    </header>

    <!-- 工作区主体 -->
    <main class="workspace">
      <el-alert
        v-if="!isBridgeReady"
        :title="t('statusConnecting')"
        type="info"
        :closable="false"
        show-icon
      />

      <div class="mode-switch-wrapper">
        <el-radio-group v-model="workMode" :disabled="processing" class="mode-radio-group">
          <el-radio-button value="dir">
            <el-icon><FolderOpened /></el-icon> {{ t('modeDir') }}
          </el-radio-button>
          <el-radio-button value="pdf">
            <el-icon><Document /></el-icon> {{ t('modePdf') }}
          </el-radio-button>
        </el-radio-group>
        <span class="mode-tip-tag" v-if="workMode === 'pdf'">
          <el-icon><Warning /></el-icon> {{ t('modePdfTip') }}
        </span>
      </div>

      <!-- 路径输入卡片 -->
      <el-card class="section-card source-card" shadow="never">
        <template #header>
          <div class="card-heading">
            <span class="heading-icon">
              <el-icon v-if="workMode === 'dir'"><FolderOpened /></el-icon>
              <el-icon v-else><Document /></el-icon>
            </span>
            <span>{{ workMode === 'dir' ? t('secDir') : t('secPdf') }}</span>
          </div>
        </template>

        <!-- 1. 图片目录模式 -->
        <template v-if="workMode === 'dir'">
          <div class="directory-row">
            <label>{{ t('labelInputDir') }}</label>
            <el-input
              v-model="form.source_dir"
              :disabled="processing"
              :placeholder="t('phInputDir')"
              clearable
            />
            <el-button
              :loading="directoryLoading === 'source_dir'"
              :disabled="processing"
              :icon="FolderOpened"
              @click="selectDirectory('source_dir')"
            >{{ t('btnBrowse') }}</el-button>
          </div>

          <div class="directory-row">
            <label>{{ t('labelOutputDir') }}</label>
            <el-input
              v-model="form.target_dir"
              :disabled="processing"
              :placeholder="t('phOutputDir')"
              clearable
            />
            <el-button
              :loading="directoryLoading === 'target_dir'"
              :disabled="processing"
              :icon="FolderOpened"
              @click="selectDirectory('target_dir')"
            >{{ t('btnBrowse') }}</el-button>
          </div>

          <div class="directory-option">
            <el-checkbox v-model="form.include_subfolders" :disabled="processing">
              {{ t('chkIncludeSub') }}
            </el-checkbox>
            <span>{{ t('tipIncludeSub') }}</span>
          </div>
        </template>

        <!-- 2. PDF 文件模式 -->
        <template v-else>
          <div class="directory-row">
            <label>{{ t('labelPdfFile') }}</label>
            <el-input
              v-model="pdfForm.pdf_path"
              :disabled="processing"
              :placeholder="t('phPdfFile')"
              clearable
            />
            <el-button
              :loading="directoryLoading === 'pdf_file'"
              :disabled="processing"
              :icon="Document"
              @click="selectPdfFileForMode"
            >{{ t('btnSelectFile') }}</el-button>
          </div>

          <div class="directory-row">
            <label>{{ t('labelGenDir') }}</label>
            <el-input
              :model-value="computedPdfTargetDir"
              disabled
              :placeholder="t('phGenDir')"
            />
            <el-tag size="default" type="info" style="margin-left: 10px; flex-shrink: 0;">
              {{ pdfForm.no_convert_pdf && !form.enable_crop && !form.enable_binarize ? t('tagSameDir') : t('tagSameDirTask') }}
            </el-tag>
          </div>

          <div class="directory-option">
            <el-checkbox v-model="pdfForm.no_convert_pdf" :disabled="processing">
              {{ t('chkNoConvertPdf') }}
            </el-checkbox>
          </div>

          <div class="directory-option pdf-hint">
            <el-icon><InfoFilled /></el-icon>
            <span v-if="pdfForm.no_convert_pdf && !form.enable_crop && !form.enable_binarize">{{ t('pdfHintExtractOnly') }}</span>
            <span v-else-if="pdfForm.no_convert_pdf">{{ t('pdfHintProcessOnly') }}</span>
            <span v-else>{{ t('pdfHintReconstruct') }}</span>
          </div>

          <div class="directory-option pdf-warning-hint">
            <el-icon><Warning /></el-icon>
            <span>{{ t('pdfWarningHint') }}</span>
          </div>
        </template>
      </el-card>

      <!-- 处理设置双卡片栅格 -->
      <div class="option-grid">
        <!-- 色彩处理卡片 -->
        <el-card class="section-card" shadow="never">
          <template #header>
            <div class="card-heading card-heading-switch">
              <span>
                <span class="heading-icon cyan"><el-icon><MagicStick /></el-icon></span>
                {{ t('secColor') }}
              </span>
              <el-switch v-model="form.enable_binarize" :disabled="processing" />
            </div>
          </template>

          <div v-if="form.enable_binarize" class="option-body">
            <div class="setting-row setting-row-top">
              <span class="setting-label">{{ t('labelBinMethod') }}</span>
              <el-radio-group v-model="form.bin_method" :disabled="processing">
                <el-radio-button value="0">{{ t('binOtsu') }}</el-radio-button>
                <el-radio-button value="1">{{ t('binCustom') }}</el-radio-button>
              </el-radio-group>
            </div>
            <div v-if="form.bin_method === '1'" class="setting-row compact-row">
              <span class="setting-label">{{ t('labelThreshold') }}</span>
              <el-input-number
                v-model="form.threshold_val"
                :min="0"
                :max="100"
                :disabled="processing"
                size="small"
              />
              <span class="unit">%</span>
            </div>
            <p class="setting-tip">{{ t('tipBinarize') }}</p>
          </div>

          <div v-else class="option-body">
            <div class="setting-row setting-row-top">
              <span class="setting-label">{{ t('labelSizeOpt') }}</span>
              <el-radio-group v-model="form.size_opt_mode" :disabled="processing">
                <el-radio value="original">{{ t('optOriginal') }}</el-radio>
                <el-radio value="mobile">{{ t('optMobile') }}</el-radio>
                <el-radio value="custom">{{ t('optCustom') }}</el-radio>
              </el-radio-group>
            </div>

            <!-- 自定义参数二级面板 -->
            <div v-if="form.size_opt_mode === 'custom'" class="custom-opt-panel">
              <div class="setting-row compact-row">
                <span class="setting-label">{{ t('labelCustomScale') }}</span>
                <el-select v-model="form.custom_scale" :disabled="processing" size="small" style="width: 110px;">
                  <el-option :value="80" label="80%" />
                  <el-option :value="60" label="60%" />
                  <el-option :value="50" label="50%" />
                  <el-option :value="40" label="40%" />
                  <el-option :value="30" label="30%" />
                  <el-option :value="20" label="20%" />
                </el-select>
              </div>
              <div class="setting-row compact-row">
                <span class="setting-label">{{ t('labelCustomQuality') }}</span>
                <el-input-number
                  v-model="form.custom_quality"
                  :min="1"
                  :max="100"
                  :disabled="processing"
                  size="small"
                />
              </div>
            </div>

            <p v-if="form.size_opt_mode === 'original'" class="setting-tip">{{ t('tipOriginal') }}</p>
            <p v-else-if="form.size_opt_mode === 'mobile'" class="setting-tip">{{ t('tipMobile') }}</p>
            <p v-else-if="form.size_opt_mode === 'custom'" class="setting-tip">{{ t('tipCustom') }}</p>
          </div>
        </el-card>

        <!-- 分页裁切卡片 -->
        <el-card class="section-card" shadow="never">
          <template #header>
            <div class="card-heading card-heading-switch">
              <span>
                <span class="heading-icon amber"><el-icon><Scissor /></el-icon></span>
                {{ t('secCrop') }}
              </span>
              <el-switch v-model="form.enable_crop" :disabled="processing" />
            </div>
          </template>

          <div v-if="form.enable_crop" class="option-body">
            <div class="setting-row">
              <span class="setting-label">{{ t('labelExcludeRatio') }}</span>
              <el-input-number
                v-model="form.exclude_ratio"
                :min="0.1"
                :max="10"
                :step="0.1"
                :precision="1"
                :disabled="processing"
                size="small"
              />
            </div>
            <div class="setting-row">
              <span class="setting-label">{{ t('labelCropPercent') }}</span>
              <el-input-number
                v-model="form.crop_percent"
                :min="1"
                :max="100"
                :disabled="processing"
                size="small"
              />
              <span class="unit">%</span>
            </div>
            <div class="setting-row reading-order">
              <span class="setting-label">{{ t('labelReadingOrder') }}</span>
              <el-radio-group v-model="form.crop_direction" :disabled="processing">
                <el-radio value="R2L">{{ t('orderR2L') }}</el-radio>
                <el-radio value="L2R">{{ t('orderL2R') }}</el-radio>
              </el-radio-group>
            </div>

            <!-- 动态线框视觉展示区 -->
            <div class="wireframe-visual-panel">
              <!-- 1. 排除单页比例线框 -->
              <div class="wireframe-item">
                <div class="wireframe-header">
                  <span class="wireframe-title">{{ t('wireframeSingleTitle') }}</span>
                  <span class="wireframe-badge">{{ t('wireframeSingleRatio', { ratio: form.exclude_ratio }) }}</span>
                </div>
                <div class="wireframe-stage">
                  <div
                    class="wireframe-single-box"
                    :style="{ width: singleBoxWidth + 'px' }"
                    :title="t('labelExcludeRatio') + ': ' + form.exclude_ratio"
                  >
                    <div class="wireframe-box-inner">
                      <el-icon><Document /></el-icon>
                      <span>{{ t('wireframeSingleOriginal') }}</span>
                    </div>
                  </div>
                </div>
                <div class="wireframe-caption">
                  {{ t('wireframeSingleCaption', { ratio: form.exclude_ratio }) }}
                </div>
              </div>

              <!-- 2. 双页分割比例与阅读顺序线框 -->
              <div class="wireframe-item">
                <div class="wireframe-header">
                  <span class="wireframe-title">{{ t('wireframeSpreadTitle') }}</span>
                  <span class="wireframe-badge">{{ t('wireframeSpreadBadge', { percent: form.crop_percent }) }}</span>
                </div>
                <div class="wireframe-stage">
                  <div class="wireframe-spread-box" :title="spreadBoxTooltip">
                    <!-- 当 P >= 50 时展示：左半页独占 + 中缝重叠 + 右半页独占 -->
                    <template v-if="cropPercentNum >= 50">
                      <div
                        class="wireframe-page wireframe-page-left"
                        :style="{ width: (100 - cropPercentNum) + '%' }"
                        :class="{ 'is-page-a': form.crop_direction === 'L2R' }"
                      >
                        <span class="page-tag">{{ form.crop_direction === 'R2L' ? t('pageTag2') : t('pageTag1') }}</span>
                        <span class="page-pct">{{ 100 - cropPercentNum }}%</span>
                      </div>

                      <div v-if="overlapPercent > 0" class="wireframe-cut-line" :style="{ left: (100 - cropPercentNum) + '%' }">
                        <span class="scissor-icon"><el-icon><Scissor /></el-icon></span>
                      </div>

                      <div
                        v-if="overlapPercent > 0"
                        class="wireframe-page wireframe-page-overlap"
                        :style="{ width: overlapPercent + '%' }"
                      >
                        <span class="overlap-tag">{{ t('wireframeOverlap') }}</span>
                        <span class="overlap-pct">{{ overlapPercent }}%</span>
                      </div>

                      <div class="wireframe-cut-line" :style="{ left: cropPercentNum + '%' }">
                        <span class="scissor-icon"><el-icon><Scissor /></el-icon></span>
                      </div>

                      <div
                        class="wireframe-page wireframe-page-right"
                        :style="{ width: (100 - cropPercentNum) + '%' }"
                        :class="{ 'is-page-a': form.crop_direction === 'R2L' }"
                      >
                        <span class="page-tag">{{ form.crop_direction === 'R2L' ? t('pageTag1') : t('pageTag2') }}</span>
                        <span class="page-pct">{{ 100 - cropPercentNum }}%</span>
                      </div>
                    </template>

                    <!-- 当 P < 50 时展示：左页 + 未裁区 + 右页 -->
                    <template v-else>
                      <div
                        class="wireframe-page wireframe-page-left"
                        :style="{ width: cropPercentNum + '%' }"
                        :class="{ 'is-page-a': form.crop_direction === 'L2R' }"
                      >
                        <span class="page-tag">{{ form.crop_direction === 'R2L' ? t('pageTag2') : t('pageTag1') }}</span>
                        <span class="page-pct">{{ cropPercentNum }}%</span>
                      </div>

                      <div class="wireframe-cut-line" :style="{ left: cropPercentNum + '%' }">
                        <span class="scissor-icon"><el-icon><Scissor /></el-icon></span>
                      </div>

                      <div class="wireframe-page wireframe-page-gap" :style="{ width: gapPercent + '%' }">
                        <span class="gap-tag">{{ t('wireframeGap') }}</span>
                        <span class="gap-pct">{{ gapPercent }}%</span>
                      </div>

                      <div class="wireframe-cut-line" :style="{ left: (100 - cropPercentNum) + '%' }">
                        <span class="scissor-icon"><el-icon><Scissor /></el-icon></span>
                      </div>

                      <div
                        class="wireframe-page wireframe-page-right"
                        :style="{ width: cropPercentNum + '%' }"
                        :class="{ 'is-page-a': form.crop_direction === 'R2L' }"
                      >
                        <span class="page-tag">{{ form.crop_direction === 'R2L' ? t('pageTag1') : t('pageTag2') }}</span>
                        <span class="page-pct">{{ cropPercentNum }}%</span>
                      </div>
                    </template>
                  </div>
                </div>
                <div class="wireframe-caption">
                  {{ spreadCaptionText }}
                </div>
              </div>
            </div>
          </div>

          <div v-else class="disabled-panel">
            <el-icon><Document /></el-icon>
            <span>{{ t('singlePageOnly') }}</span>
          </div>
        </el-card>
      </div>

      <!-- 执行与进度卡片 -->
      <el-card class="section-card execution-card" shadow="never">
        <template #header>
          <div class="card-heading">
            <span class="heading-icon violet"><el-icon><Setting /></el-icon></span>
            <span>{{ t('secExecution') }}</span>
          </div>
        </template>

        <div class="execution-options">
          <div class="inline-setting">
            <span>{{ t('labelMaxThreads') }}</span>
            <el-input-number
              v-model="form.max_threads"
              :min="1"
              :max="64"
              :disabled="processing"
              size="small"
            />
          </div>
          <template v-if="workMode === 'dir'">
            <el-checkbox v-model="form.enable_pdf" :disabled="processing">
              {{ t('chkEnablePdf') }}
            </el-checkbox>
            <el-checkbox
              v-if="form.enable_pdf && (form.enable_crop || form.enable_binarize || form.size_opt_mode !== 'original')"
              v-model="form.keep_images_after_pdf"
              :disabled="processing"
              style="margin-left: 15px;"
            >
              {{ t('chkKeepImages') }}
            </el-checkbox>
            <el-tag
              v-if="form.enable_pdf && !form.enable_crop && !form.enable_binarize && form.size_opt_mode === 'original'"
              size="small"
              type="success"
              effect="plain"
              style="margin-left: 10px;"
            >
              <el-icon><Check /></el-icon> {{ t('tagDirectPdf') }}
            </el-tag>
            <el-tag
              v-else-if="form.enable_pdf && !form.enable_crop && !form.enable_binarize && form.size_opt_mode === 'mobile'"
              size="small"
              type="warning"
              effect="plain"
              style="margin-left: 10px;"
            >
              <el-icon><Document /></el-icon> {{ t('tagMobilePdf') }}
            </el-tag>
            <el-tag
              v-else-if="form.enable_pdf && !form.enable_crop && !form.enable_binarize && form.size_opt_mode === 'custom'"
              size="small"
              type="warning"
              effect="plain"
              style="margin-left: 10px;"
            >
              <el-icon><Document /></el-icon> {{ t('tagCustomPdf') }}
            </el-tag>
          </template>
          <template v-else>
            <el-checkbox v-model="pdfForm.no_convert_pdf" :disabled="processing">
              {{ t('chkNoConvertPdf') }}
            </el-checkbox>
            <el-tag v-if="!pdfForm.no_convert_pdf" size="small" type="success" effect="plain" style="margin-left: 10px;">
              <el-icon><Check /></el-icon> {{ t('tagPdfReconstruct') }}
            </el-tag>
            <el-tag v-else-if="!form.enable_crop && !form.enable_binarize" size="small" type="warning" effect="plain" style="margin-left: 10px;">
              <el-icon><Download /></el-icon> {{ t('tagPdfExtractOnly') }}
            </el-tag>
            <el-tag v-else size="small" type="warning" effect="plain" style="margin-left: 10px;">
              <el-icon><FolderOpened /></el-icon> {{ t('tagPdfProcessOnly') }}
            </el-tag>
          </template>
        </div>

        <div class="progress-panel">
          <div class="progress-meta">
            <span>{{ phaseText }}</span>
            <strong>{{ Math.round(progress) }}%</strong>
          </div>
          <el-progress
            :percentage="Math.round(progress)"
            :stroke-width="10"
            :show-text="false"
            color="#2f80ed"
          />
          <div class="status-line" :class="{ active: processing }">
            <el-icon :class="{ 'is-loading': processing }">
              <Loading v-if="processing" />
              <InfoFilled v-else />
            </el-icon>
            <span>{{ status }}</span>
          </div>
        </div>

        <div class="action-row">
          <el-button
            type="danger"
            plain
            :disabled="!processing || phase === 'cancelling'"
            @click="cancelTask"
          >{{ t('btnCancel') }}</el-button>
          
          <el-button
            :disabled="!canPause"
            @click="togglePause"
          >{{ paused ? t('btnResume') : t('btnPause') }}</el-button>
          
          <el-button
            type="primary"
            :icon="VideoPlay"
            :disabled="!isBridgeReady || processing"
            @click="startTask"
          >{{ t('btnStart') }}</el-button>
        </div>
      </el-card>
    </main>

    <!-- 软件底部版权作者信息 -->
    <footer class="app-footer">
      <span>By weiceng &copy; 漢籍合璧</span>
    </footer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Reading,
  ArrowDown,
  Check,
  Bell,
  Download,
  FolderOpened,
  Document,
  Warning,
  InfoFilled,
  MagicStick,
  Scissor,
  Setting,
  VideoPlay,
  Loading,
  UploadFilled
} from '@element-plus/icons-vue'
import { useI18n } from './locales/i18n.js'
import { isBridgeReady, initBridge, callApi } from './api/bridge.js'

const { currentLang, currentLanguageLabel, supportedLanguages, t, setLanguage } = useI18n()

// 状态定义
const isDragging = ref(false)
const updateAvailable = ref(false)
const updateInfo = ref({})
const directoryLoading = ref('')
const processing = ref(false)
const paused = ref(false)
const phase = ref('idle')
const progress = ref(0)
const status = ref(t('statusReady'))
const workMode = ref('dir')
let pollTimer = null
let isPolling = false

const pdfForm = reactive({
  pdf_path: '',
  no_convert_pdf: false
})

const form = reactive({
  source_dir: '',
  target_dir: '',
  include_subfolders: false,
  keep_images_after_pdf: false,
  enable_binarize: true,
  bin_method: '0',
  threshold_val: 50,
  size_opt_mode: 'original',
  custom_scale: 80,
  custom_quality: 80,
  non_bin_format: 'keep',
  enable_crop: true,
  crop_percent: 50,
  crop_direction: 'R2L',
  exclude_ratio: 0.7,
  max_threads: 8,
  enable_pdf: false
})

// 计算属性
const canPause = computed(() => {
  return processing.value && phase.value === 'processing'
})

const singleBoxWidth = computed(() => {
  const ratio = parseFloat(form.exclude_ratio)
  const r = isNaN(ratio) || ratio <= 0 ? 0.7 : ratio
  return Math.min(84, Math.max(16, Math.round(48 * r)))
})

const cropPercentNum = computed(() => {
  const pct = parseInt(form.crop_percent, 10)
  return isNaN(pct) || pct < 1 || pct > 100 ? 50 : pct
})

const overlapPercent = computed(() => {
  return Math.max(0, 2 * cropPercentNum.value - 100)
})

const gapPercent = computed(() => {
  return Math.max(0, 100 - 2 * cropPercentNum.value)
})

const spreadBoxTooltip = computed(() => {
  const p = cropPercentNum.value
  if (p > 50) {
    return t('tooltipOverlap', { crop: p, overlap: overlapPercent.value })
  } else if (p === 50) {
    return t('tooltipEqual')
  } else {
    return t('tooltipGap', { crop: p, gap: gapPercent.value })
  }
})

const spreadCaptionText = computed(() => {
  const p = cropPercentNum.value
  const orderText = form.crop_direction === 'R2L' ? t('captionR2L') : t('captionL2R')
  if (p > 50) {
    return t('captionOverlap', { crop: p, overlap: overlapPercent.value, order: orderText })
  } else if (p === 50) {
    return t('captionEqual', { order: orderText })
  } else {
    return t('captionGap', { crop: p, gap: gapPercent.value, order: orderText })
  }
})

const computedPdfTargetDir = computed(() => {
  if (!pdfForm.pdf_path) return ''
  const path = pdfForm.pdf_path
  const lastSlash = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  const dir = lastSlash >= 0 ? path.substring(0, lastSlash) : ''
  const filename = lastSlash >= 0 ? path.substring(lastSlash + 1) : path
  const dotIdx = filename.lastIndexOf('.')
  const stem = dotIdx >= 0 ? filename.substring(0, dotIdx) : filename
  let suffix = ''
  if (form.enable_crop && form.enable_binarize) {
    suffix = t('pdfSuffixCroppedBin')
  } else if (form.enable_crop) {
    suffix = t('pdfSuffixCropped')
  } else if (form.enable_binarize) {
    suffix = t('pdfSuffixBin')
  } else if (form.size_opt_mode === 'mobile') {
    suffix = t('pdfSuffixMobile')
  } else if (form.size_opt_mode === 'custom') {
    suffix = t('pdfSuffixCustom')
  }
  if (!suffix) {
    if (pdfForm.no_convert_pdf) {
      return (dir ? dir + '\\' : '') + stem
    }
    return t('pdfSuffixNone')
  }
  return (dir ? dir + '\\' : '') + stem + suffix
})

const phaseText = computed(() => {
  const map = {
    idle: t('phaseIdle'),
    extracting_pdf: t('phaseExtractingPdf'),
    processing: paused.value ? t('phasePaused') : t('phaseProcessing'),
    awaiting_pdf: t('phaseAwaitingPdf'),
    pdf: t('phasePdf'),
    cancelling: t('phaseCancelling'),
    finishing: t('phaseFinishing')
  }
  return map[phase.value] || t('phaseIdle')
})

// 错误弹窗
function showError(error) {
  const message = error && (error.error || error.message)
    ? (error.error || error.message)
    : String(error || t('errUnknown'))
  ElMessage.error({ message, duration: 5000, showClose: true })
}

// 切换语言
function changeLanguage(lang) {
  setLanguage(lang)
  callApi('set_user_language', lang).catch(() => {})
  if (!processing.value) {
    status.value = t('statusReady')
  }
}

// 检查更新
async function checkForUpdates() {
  try {
    const result = await callApi('get_update_info')
    if (result && result.ok && result.update && result.update.version) {
      updateInfo.value = result.update
      updateAvailable.value = compareVersions(result.current_version, result.update.version) < 0
    }
  } catch (e) {}
}

function compareVersions(current, latest) {
  const a = String(current || '0').split('.')
  const b = String(latest || '0').split('.')
  const length = Math.max(a.length, b.length)
  for (let i = 0; i < length; i++) {
    const av = parseInt(a[i] || '0', 10) || 0
    const bv = parseInt(b[i] || '0', 10) || 0
    if (av !== bv) return av < bv ? -1 : 1
  }
  return 0
}

async function downloadUpdate() {
  try {
    const result = await callApi('open_download_url', updateInfo.value.download_url)
    if (!result.ok) showError(result)
  } catch (err) {
    showError(err)
  }
}

// 拖拽处理
function handleDragOver(e) {
  e.preventDefault()
  isDragging.value = true
}

function handleDragLeave(e) {
  e.preventDefault()
  isDragging.value = false
}

function handleDrop(e) {
  e.preventDefault()
  isDragging.value = false
  if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
    const file = e.dataTransfer.files[0]
    const p = file.path || file.name
    if (p) {
      applyDroppedPath(p)
    }
  }
}

async function applyDroppedPath(path) {
  try {
    const result = await callApi('handle_dropped_path', path)
    if (!result || !result.ok) {
      showError(result ? result.error : t('errUnrecognizedDropPath'))
      return
    }
    if (result.type === 'dir') {
      workMode.value = 'dir'
      form.source_dir = result.path
      form.target_dir = result.suggested_target_dir
      ElMessage.success(t('loadedInputDir') + ': ' + result.path)
    } else if (result.type === 'pdf') {
      workMode.value = 'pdf'
      pdfForm.pdf_path = result.path
      ElMessage.success(t('loadedPdfFile') + ': ' + result.path)
    } else if (result.type === 'image') {
      workMode.value = 'dir'
      form.source_dir = result.parent_dir
      form.target_dir = result.suggested_target_dir
      ElMessage.success(t('loadedImageParentDir') + ': ' + result.parent_dir)
    }
  } catch (err) {
    showError(err)
  }
}

// 选择目录与 PDF 文件
async function selectDirectory(field) {
  directoryLoading.value = field
  try {
    const result = await callApi('choose_directory', form[field] || '')
    directoryLoading.value = ''
    if (!result.ok) {
      showError(result)
      return
    }
    if (!result.path) return
    form[field] = result.path
    if (field === 'source_dir') {
      form.target_dir = result.path.replace(/[\\/]+$/, '') + '\\output'
    }
  } catch (err) {
    directoryLoading.value = ''
    showError(err)
  }
}

async function selectPdfFileForMode() {
  directoryLoading.value = 'pdf_file'
  try {
    const result = await callApi('choose_pdf_file', pdfForm.pdf_path || '')
    directoryLoading.value = ''
    if (!result.ok) {
      showError(result)
      return
    }
    if (result.cancelled) return
    pdfForm.pdf_path = result.pdf_path
  } catch (err) {
    directoryLoading.value = ''
    showError(err)
  }
}

// 开始处理
async function startTask() {
  if (workMode.value === 'pdf') {
    if (!pdfForm.pdf_path) {
      showError(t('errSelectPdf'))
      return
    }
    if (!form.enable_crop && !form.enable_binarize && form.size_opt_mode === 'original' && !pdfForm.no_convert_pdf) {
      showError(t('errSelectTask'))
      return
    }
    const targetDir = computedPdfTargetDir.value
    form.non_bin_format = form.size_opt_mode === 'original' ? 'keep' : 'jpg80'
    const pdfSettings = Object.assign({}, form, {
      pdf_path: pdfForm.pdf_path,
      no_convert_pdf: pdfForm.no_convert_pdf,
      lang: currentLang.value
    })

    const runWorkflow = async () => {
      try {
        const result = await callApi('start_pdf_workflow', pdfSettings)
        if (!result.ok) {
          showError(result)
          return
        }
        processing.value = true
        paused.value = false
        phase.value = 'extracting_pdf'
        progress.value = 0
        status.value = t('statusStartingWorkflow')
      } catch (err) {
        showError(err)
      }
    }

    let confirmMessage = ''
    if (pdfForm.no_convert_pdf && !form.enable_crop && !form.enable_binarize && form.size_opt_mode === 'original') {
      confirmMessage = t('pdfWorkflowMsgExtract', { dir: targetDir })
    } else if (pdfForm.no_convert_pdf) {
      confirmMessage = t('pdfWorkflowMsgProcess', { dir: targetDir })
    } else {
      confirmMessage = t('pdfWorkflowMsgReconstruct', { dir: targetDir })
    }

    try {
      await ElMessageBox.confirm(confirmMessage, t('pdfWorkflowConfirmTitle'), {
        type: 'info',
        confirmButtonText: t('pdfWorkflowConfirmStart'),
        cancelButtonText: t('btnCancelGeneral'),
        closeOnClickModal: false
      })
      runWorkflow()
    } catch (e) {}
    return
  }

  // 图片目录模式
  if (!form.source_dir) {
    showError(t('errSelectSourceDir'))
    return
  }
  if (!form.enable_crop && !form.enable_binarize && form.size_opt_mode === 'original' && !form.enable_pdf) {
    showError(t('errSelectAnyTask'))
    return
  }

  form.non_bin_format = form.size_opt_mode === 'original' ? 'keep' : 'jpg80'
  form.lang = currentLang.value
  try {
    const result = await callApi('start_processing', form)
    if (!result.ok) {
      showError(result)
      return
    }
    processing.value = true
    paused.value = false
    phase.value = 'processing'
    progress.value = 0
    status.value = t('statusScanning')
    form.target_dir = result.target_dir
  } catch (err) {
    showError(err)
  }
}

// 暂停与取消
async function togglePause() {
  try {
    const result = await callApi('toggle_pause')
    if (!result.ok) {
      showError(result)
      return
    }
    paused.value = result.paused
    status.value = result.message
  } catch (err) {
    showError(err)
  }
}

async function cancelTask() {
  try {
    await ElMessageBox.confirm(t('confirmCancelMsg'), t('confirmCancelTitle'), {
      type: 'warning',
      confirmButtonText: t('btnConfirmCancel'),
      cancelButtonText: t('btnContinueProcessing'),
      closeOnClickModal: false
    })
    const result = await callApi('cancel_processing')
    if (!result.ok) {
      showError(result)
      return
    }
    paused.value = false
    phase.value = 'cancelling'
    status.value = result.message
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') {
      showError(err)
    }
  }
}

async function answerPdfPrompt(generatePdf) {
  try {
    const result = await callApi('respond_pdf_prompt', generatePdf)
    if (!result.ok) {
      showError(result)
      return
    }
    phase.value = generatePdf ? 'pdf' : 'finishing'
    if (generatePdf) {
      progress.value = 0
    }
    status.value = result.message
  } catch (err) {
    showError(err)
  }
}

async function askForPdf(includeSubfolders) {
  const message = includeSubfolders ? t('confirmPdfMsgSub') : t('confirmPdfMsgSingle')
  try {
    await ElMessageBox.confirm(message, t('confirmPdfTitle'), {
      type: 'info',
      confirmButtonText: t('btnGeneratePdf'),
      cancelButtonText: t('btnKeepImages'),
      distinguishCancelAndClose: true,
      closeOnClickModal: false
    })
    answerPdfPrompt(true)
  } catch (action) {
    answerPdfPrompt(false)
  }
}

async function finishTask(event) {
  processing.value = false
  paused.value = false
  phase.value = 'idle'
  progress.value = 100
  status.value = event.message
  try {
    await ElMessageBox.alert(event.message, t('taskFinishedTitle'), {
      confirmButtonText: t('btnConfirm'),
      type: event.can_open_output ? 'success' : 'info',
      customClass: 'result-dialog'
    })
    if (event.can_open_output) {
      const res = await callApi('open_output_folder')
      if (res && !res.ok) showError(res)
    }
  } catch (err) {}
}

async function handlePdfExtracted(event) {
  processing.value = false
  paused.value = false
  phase.value = 'idle'
  if (event.error) {
    status.value = t('errPdfExtract') + ': ' + event.error
    showError(event.error)
    return
  }
  progress.value = 100
  status.value = t('pdfExtractSuccess', { count: event.count })
  form.source_dir = event.extract_dir
  form.target_dir = event.extract_dir.replace(/[\\/]+$/, '') + '\\output'

  const msg = t('pdfExtractedFollowupMsg', { count: event.count, dir: event.extract_dir })
  try {
    await ElMessageBox.confirm(msg, t('pdfExtractedFollowupTitle'), {
      type: 'success',
      confirmButtonText: t('btnConfirm'),
      cancelButtonText: t('btnCancelGeneral'),
      closeOnClickModal: false
    })
    startTask()
  } catch (e) {}
}

function handleEvent(event) {
  if (event.type === 'progress') {
    progress.value = Math.max(0, Math.min(100, event.percent))
    status.value = event.message
  } else if (event.type === 'status') {
    status.value = event.message
    if (event.message.includes('PDF')) {
      phase.value = 'pdf'
      progress.value = 0
    }
  } else if (event.type === 'ask_pdf') {
    progress.value = 100
    phase.value = 'awaiting_pdf'
    status.value = t('phaseAwaitingPdf')
    askForPdf(event.include_subfolders)
  } else if (event.type === 'pdf_extracted') {
    handlePdfExtracted(event)
  } else if (event.type === 'finish') {
    finishTask(event)
  }
}

async function pollEvents() {
  if (!isBridgeReady.value || isPolling) return
  isPolling = true
  try {
    const result = await callApi('poll_events')
    isPolling = false
    if (!result || !result.ok) return
    processing.value = result.processing
    paused.value = result.paused
    phase.value = result.phase
    if (result.events && Array.isArray(result.events)) {
      for (const ev of result.events) {
        handleEvent(ev)
      }
    }
  } catch (err) {
    isPolling = false
  }
}

onMounted(async () => {
  // 挂载全局供 pywebview COM 拖拽直接调用的 hook
  window.c2bwDropPath = (p) => applyDroppedPath(p)

  await initBridge()
  checkForUpdates()

  // 同步用户语言设置
  try {
    const langRes = await callApi('get_user_language')
    if (langRes && langRes.ok && langRes.language) {
      setLanguage(langRes.language)
    }
  } catch (e) {}

  pollTimer = setInterval(pollEvents, 250)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
