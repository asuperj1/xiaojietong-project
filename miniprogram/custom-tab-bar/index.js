// F12 POC：自定义 TabBar（5 项 + AI 居中凸起圆形 + 长按唤起语音）
//
// 依据：docs/二阶段整改方案-前端UI重构与后端支撑.md §1.4（原生 tabBar 无法做凸起按钮 → 自定义）
//      docs/成员任务单-二阶段整改260912.md §3.3 F12（**先做 POC**）
//
// 本 POC 只验证 5 件事（其余留给 F12 正式实现 / F14）：
//   ① 5 项可显示并可切换        ② AI 居中凸起圆形
//   ③ 长按 AI 的语音触发链路    ④ 震动 + 弹窗反馈
//   ⑤ 全屏页隐藏机制（配合 utils/tabbar.js）
//
// ⚠️ POC 边界（刻意不做，避免未经验证铺开）：
//   - **不含图标**：F11 的线性 SVG 图标集**已合入 dev**（`static/icons/*.svg`，由
//     tools/verify_f11_icon_set.js 守护）；但本 POC 仍刻意不放图标 —— 接入图标会改变每项
//     高度，"真机凸起位置正确"这条结论就不可迁移了（故只按 §1.5 预留 48rpx 槽位）。
//     真正接图标属 F12 正式实现。
//   - **不接入转写**：转写接口**已就绪**（`B32`，即方案 §3.4 所写的 `B28`：
//     POST /voice/transcribe，契约见 docs/api.md §13），但本 POC **刻意不调用** ——
//     这里只验证「长按 → 震动 → 弹窗」链路。弹窗内只做「录音自检」，不上传。
//   - 真机布局 / 震动 / 弹窗 / 隐藏效果 = MANUAL CHECK REQUIRED。

const { TAB_LIST } = require('../utils/tab-order')

// 录音自检时长：弹窗文案与 start() 共用同一个值，
// 避免"文案说 2.5 秒、实际录 3 秒"这种只有真机才发现的漂移。
const RECORDER_CHECK_MS = 2500

Component({
  data: {
    selected: 0, // 当前选中项，由各 Tab 页 onShow 调 setSelected() 写入
    hidden: false, // 全屏态隐藏（POC 由 AI 页顶部按钮触发，见 utils/tabbar.js）
    glassFallback: false, // F10 能力探测结果：不支持毛玻璃时走实心降级
    // 5 项与顺序来自 utils/tab-order.js（JS 侧唯一事实来源），
    // 仍需与 app.json 的 tabBar.list 严格一致（由 tools/verify_f12_tabbar_poc.js 断言）。
    // ⚠️ 顺序按方案 §1.4「AI 助手置于正中」：AI 在 5 项的第 3 位（index 2），
    //    否则「中部凸起圆形」会偏左（这也是 F12 相对现状的一处**有意的顺序调整**）。
    list: TAB_LIST,
  },

  lifetimes: {
    attached() {
      // F10：把「能力探测结果」翻译成玻璃样式库的运行时降级类（progressive enhancement 第三层）
      const app = getApp()
      const supported = !!(app && app.globalData && app.globalData.glassSupported)
      this.setData({ glassFallback: !supported })

      // 录音器只在 attached 注册一次监听：RecorderManager 的 on* 是「追加监听」语义，
      // 每次长按都注册会导致回调叠加、重复弹窗。
      this._recorder = typeof wx.getRecorderManager === 'function' ? wx.getRecorderManager() : null
      if (this._recorder) {
        this._recorder.onStop((res) => {
          this._finishRecorderCheck(
            `录音成功：时长 ${Math.round((res.duration || 0) / 1000)} 秒，` +
              `大小 ${res.fileSize || 0} 字节。\n本 POC 不上传、不转写（转写接口 B32 已就绪，接入属下一步）。`
          )
        })
        this._recorder.onError((err) => {
          this._finishRecorderCheck(
            `录音失败：${(err && err.errMsg) || '未知错误'}\n常见原因：未授权 scope.record 或当前环境不支持。`
          )
        })
      }
    },
  },

  methods: {
    /** 供 Tab 页 onShow 调用：this.getTabBar().setSelected(0) */
    setSelected(selected) {
      if (this.data.selected !== selected) this.setData({ selected })
    },

    /** 供 utils/tabbar.js 调用：全屏态显隐 */
    setHidden(hidden) {
      const next = !!hidden
      if (this.data.hidden !== next) this.setData({ hidden: next })
    },

    onItemTap(e) {
      const { path } = e.currentTarget.dataset
      if (!path) return
      wx.switchTab({
        url: path,
        // 失败只 console.error 的话，用户看到的就是"点了没反应"（底栏是这个页面上唯一的导航出口）。
        // 与 pages/map 等页面的既有做法一致：失败要给可见反馈。
        fail: (err) => {
          console.error('[tabbar-poc] switchTab 失败：', path, err)
          wx.showToast({ title: '切换失败，请重试', icon: 'none' })
        },
      })
    },

    /**
     * 长按：仅 AI 项唤起语音（POC 验证触发链路）。
     * `data-raised="{{item.raised}}"` 在 dataset 里可能是布尔 true、也可能是字符串 'true'
     * （模板插值经 dataset 往返后会字符串化），故两种都认。
     */
    onItemLongPress(e) {
      const { raised } = e.currentTarget.dataset
      if (raised !== true && raised !== 'true') return
      this.openVoiceEntry()
    },

    openVoiceEntry() {
      // ① 触觉反馈。振动属「锦上添花」，失败（开发者工具/部分机型无振动器）不得中断链路。
      try {
        wx.vibrateShort({ type: 'medium', fail: () => {} })
      } catch (err) {
        console.warn('[tabbar-poc] vibrateShort 不可用：', err)
      }

      // ② 语音入口弹窗（POC 的可观测证据）
      wx.showModal({
        title: '语音输入（POC）',
        content:
          '长按触发链路已生效：震动 → 语音入口。\n' +
          '转写接口已就绪（B32：POST /voice/transcribe），但本 POC 不接入 —— 只验证长按链路。\n' +
          '可点「录音自检」验证本机录音能力（' +
          `${RECORDER_CHECK_MS / 1000} 秒后自动停止，不上传）。`,
        confirmText: '录音自检',
        cancelText: '关闭',
        success: (res) => {
          if (res.confirm) this.runRecorderSelfCheck()
        },
      })
    },

    runRecorderSelfCheck() {
      if (!this._recorder) {
        wx.showModal({
          title: '录音不可用',
          content: '当前环境不支持 wx.getRecorderManager。',
          showCancel: false,
        })
        return
      }
      this._recorderPending = true
      // start() 在部分环境会**同步抛错**（vibrateShort 已按同样理由兜底）。
      // 不兜底的话 _recorderPending 会永久停在 true，之后真正的回调会被
      // _finishRecorderCheck 当成"非本次自检的迟到回调"丢掉 —— 表现为静默无反应。
      try {
        this._recorder.start({
          duration: RECORDER_CHECK_MS, // 上限到点自动 stop → onStop
          format: 'mp3',
          sampleRate: 16000,
          numberOfChannels: 1,
          encodeBitRate: 48000,
        })
      } catch (err) {
        this._finishRecorderCheck(
          `录音启动失败：${(err && err.errMsg) || err}\n常见原因：未授权 scope.record 或当前环境不支持。`
        )
      }
    },

    _finishRecorderCheck(message) {
      if (!this._recorderPending) return // 丢弃非本次自检的迟到回调
      this._recorderPending = false
      wx.showModal({ title: '录音自检结果', content: message, showCancel: false })
    },
  },
})
