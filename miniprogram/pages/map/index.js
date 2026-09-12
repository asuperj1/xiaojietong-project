// 校园地图（F9-C）：POI 列表 / 附近 / 导航 / 建筑详情入口
const { request } = require('../../services/request')

const CATS = ['教学楼', '图书馆', '食堂', '宿舍', '校医院', '校车点']

Page({
  data: {
    cats: ['全部'].concat(CATS),
    catIndex: 0,
    q: '',
    items: [],
    loading: false,
    error: '',
    // 后端暂无建筑列表接口，这里用种子数据中的建筑 id 提供「建筑详情」入口（真实接口 /map/building/{id}）
    buildings: [
      { id: 1, name: '中心图书馆' },
      { id: 2, name: '第二教学楼' },
    ],
  },

  onLoad() {
    this.fetch()
  },

  onCatChange(e) {
    // 分类 tab 是 bindtap（不是 picker）：索引来自 data-index，e.detail 中没有 value
    const index = Number(e.currentTarget.dataset.index)
    if (!Number.isInteger(index) || index < 0 || index >= this.data.cats.length) return
    this.setData({ catIndex: index }, () => this.fetch())
  },

  onSearchInput(e) {
    this.setData({ q: e.detail.value })
  },
  onSearch() {
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    const cat = this.data.catIndex === 0 ? '' : this.data.cats[this.data.catIndex]
    request('/map/pois', { data: { category: cat } })
      .then((res) => {
        let items = (res && res.items) || []
        const q = (this.data.q || '').trim()
        if (q) items = items.filter((it) => (it.name || '').indexOf(q) !== -1)
        items = items.map((it) => {
          // 后端 poi.id 可能为字符串：统一归一化为数字，否则 onPoiTap 严格相等匹配不上
          const numId = Number(it.id)
          return { ...it, id: Number.isFinite(numId) ? numId : it.id }
        })
        this.setData({ items, loading: false })
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },

  onNearby() {
    wx.getLocation({
      type: 'gcj02',
      success: (loc) => {
        this.setData({ loading: true, error: '' })
        request('/map/nearby', {
          data: { lat: loc.latitude, lng: loc.longitude, radius: 1000 },
        })
          .then((res) => {
            const items = ((res && res.items) || []).map((it) => {
              const numId = Number(it.id)
              return { ...it, id: Number.isFinite(numId) ? numId : it.id }
            })
            this.setData({ items, loading: false })
          })
          .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
      },
      fail: () => wx.showToast({ title: '无法获取位置', icon: 'none' }),
    })
  },

  onPoiTap(e) {
    const id = Number(e.currentTarget.dataset.id)
    const poi = this.data.items.find((p) => p.id === id)
    wx.showActionSheet({
      itemList: ['到这去', '查看信息'],
      success: (r) => {
        if (r.tapIndex === 0) {
          request('/map/navigate', { method: 'POST', data: { to_poi_id: id } })
            .then((res) => {
              wx.showModal({
                title: '导航',
                content: '距离约 ' + (res.distance || 0) + ' 米，步行约 ' + (res.duration || 0) + ' 分钟',
                showCancel: false,
              })
            })
            .catch(() => {})
        } else if (poi) {
          wx.showModal({
            title: poi.name || '',
            content: '分类：' + (poi.category || '') + '\n楼层：' + (poi.floor != null ? poi.floor : ''),
            showCancel: false,
          })
        }
      },
    })
  },

  onBuildingTap(e) {
    wx.navigateTo({ url: '/pages/map/building?id=' + e.currentTarget.dataset.id })
  },
})
