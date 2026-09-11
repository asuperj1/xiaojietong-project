// 任务中心（F9-E）：Agent 任务 + 提醒
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

const STATUS_TEXT = { 0: '待执行', 1: '执行中', 2: '成功', 3: '失败', 4: '已取消' }

Page({
  data: {
    tab: 'task',
    instruction: '',
    tasks: [],
    reminderContent: '',
    reminderAt: '',
    reminders: [],
    loading: true,
    error: '',
    submitting: false,
  },

  onShow() {
    this.loadTab()
  },

  onTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab }, () => this.loadTab())
  },

  loadTab() {
    if (this.data.tab === 'task') this.fetchTasks()
    else this.fetchReminders()
  },

  fetchTasks() {
    this.setData({ loading: true, error: '' })
    request('/agent/tasks', { data: { page: 1, size: 20 } })
      .then((res) => {
        const tasks = ((res && res.items) || []).map((t) => ({
          ...t,
          statusText: STATUS_TEXT[Number(t.status)] || '未知',
          time: formatTime(t.created_at),
          cancelable: Number(t.status) === 0 || Number(t.status) === 1,
        }))
        this.setData({ tasks, loading: false })
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },

  fetchReminders() {
    this.setData({ loading: true, error: '' })
    request('/agent/reminders')
      .then((res) => {
        const reminders = ((res && res.items) || []).map((r) => ({
          ...r,
          time: formatTime(r.remind_at),
          done: !!r.is_done,
        }))
        this.setData({ reminders, loading: false })
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },

  onInstruction(e) {
    this.setData({ instruction: e.detail.value })
  },

  onCreateTask() {
    const instruction = (this.data.instruction || '').trim()
    if (!instruction) {
      wx.showToast({ title: '请输入任务指令', icon: 'none' })
      return
    }
    if (this.data.submitting) return
    this.setData({ submitting: true })
    request('/agent/tasks', { method: 'POST', data: { instruction } })
      .then((res) => {
        this.setData({ instruction: '', submitting: false })
        const plan = (res && res.plan) || []
        const text = plan
          .map((p) => p.desc + (p.result ? ' → ' + p.result : p.error ? ' → 失败：' + p.error : ''))
          .join('\n')
        wx.showModal({
          title: '任务已创建',
          content: text || (res && res.status === 3 ? '任务执行失败' : '任务已提交'),
          showCancel: false,
        })
        this.fetchTasks()
      })
      .catch(() => this.setData({ submitting: false }))
  },

  onTaskTap(e) {
    const task = this.data.tasks.find((t) => t.id === Number(e.currentTarget.dataset.id))
    if (!task) return
    wx.showModal({
      title: task.title || '任务',
      content: '状态：' + task.statusText + (task.error_msg ? '\n' + task.error_msg : ''),
      showCancel: false,
    })
  },

  onCancelTask(e) {
    const id = e.currentTarget.dataset.id
    request('/agent/tasks/' + id + '/cancel', { method: 'POST' })
      .then(() => this.fetchTasks())
      .catch(() => {})
  },

  onReminderContent(e) {
    this.setData({ reminderContent: e.detail.value })
  },
  onReminderAt(e) {
    this.setData({ reminderAt: e.detail.value })
  },

  onAddReminder() {
    const content = (this.data.reminderContent || '').trim()
    const remindAt = (this.data.reminderAt || '').trim()
    if (!content) {
      wx.showToast({ title: '请输入提醒内容', icon: 'none' })
      return
    }
    if (this.data.submitting) return
    this.setData({ submitting: true })
    request('/agent/reminders', { method: 'POST', data: { content, remind_at: remindAt } })
      .then(() => {
        this.setData({ reminderContent: '', reminderAt: '', submitting: false })
        this.fetchReminders()
      })
      .catch(() => this.setData({ submitting: false }))
  },

  onDoneReminder(e) {
    const id = e.currentTarget.dataset.id
    request('/agent/reminders/' + id + '/done', { method: 'PUT' })
      .then(() => this.fetchReminders())
      .catch(() => {})
  },
})
