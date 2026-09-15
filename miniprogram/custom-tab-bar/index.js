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
//   - **不含图标**：F11 的线性 SVG 图标集尚未合入 dev；也刻意不复用即将被 F11 淘汰的彩色 PNG。
//     图标接入待 F11 合入 dev 后跟进（或由 F12 正式实现一并完成）。
//   - **不做转写**：POST /voice/transcribe（B28）未就绪，弹窗内只做「录音自检」，不上传。
//   - 真机布局 / 震动 / 弹窗 / 隐藏效果 = MANUAL CHECK REQUIRED。

Component({  data: {
    selected: 0, // 当前选中项，由各 Tab 页 onShow 调 setSelected() 写入
    hidden: false, // 全屏态隐藏（POC 由 AI 页顶部按钮触发，见 utils/tabbar.js）
    glassFallback: false, // F10 能力探测结果：不支持毛玻璃时走实心降级
    // 5 项：必须与 app.json 的 tabBar.list 严格一致（由 tools/verify_f12_tabbar_poc.js 断言）
    // ⚠️ 顺序按方案 §1.4「AI 助手置于正中」：AI 必须在 5 项的第 3 位（index 2），
    //    否则「中部凸起圆形」会偏左（这也是 F12 相对现状的一处**有意的顺序调整**）。
    list: [
      { key: 'index', pagePath: '/pages/index/index', text: '首页' },
      { key: 'service', pagePath: '/pages/service/service', text: '服务' },
      { key: 'chat', pagePath: '/pages/chat/chat', text: 'AI助手', raised: true },
      { key: 'forum', pagePath: '/pages/forum/forum', text: '论坛' },
      { key: 'user', pagePath: '/pages/user/user', text: '我的' },
    ],
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
              `大小 ${res.fileSize || 0} 字节。\n转写待 B28（POST /voice/transcribe）就绪后接入。`
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
        fail: (err) => {
          console.error('[tabbar-poc] switchTab 失败：', path, err)
        },
      })
    },

    /** 长按：仅 AI 项唤起语音（POC 验证触发链路） */
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
          '转写需 POST /voice/transcribe（B28，未就绪）。\n' +
          '可点「录音自检」验证本机录音能力（2.5 秒后自动停止，不上传）。',
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
      this._recorder.start({
        duration: 2500, // 上限 2.5s，自动 stop → onStop
        format: 'mp3',
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: 48000,
      })
    },

    _finishRecorderCheck(message) {
      if (!this._recorderPending) return // 丢弃非本次自检的迟到回调
      this._recorderPending = false
      wx.showModal({ title: '录音自检结果', content: message, showCancel: false })
    },
  },
})
